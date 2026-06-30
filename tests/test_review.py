import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REVIEW = SCRIPTS / "_review.py"
sys.path.insert(0, str(SCRIPTS))
from _review import _detected_recorder  # noqa: E402


class DetectedRecorderTests(unittest.TestCase):
    def test_codex_marker_wins_over_inherited_claudecode(self):
        self.assertEqual(
            _detected_recorder("Codex", {
                "CODEX_SANDBOX": "seatbelt",
                "CLAUDECODE": "1",
            }),
            "Codex",
        )

    def test_claude_code_marker_detects_claude(self):
        self.assertEqual(
            _detected_recorder("Claude", {"CLAUDE_CODE_ENTRYPOINT": "cli"}),
            "Claude",
        )

    def test_codex_thread_id_detects_codex(self):
        self.assertEqual(
            _detected_recorder("Codex", {"CODEX_THREAD_ID": "thread-123"}),
            "Codex",
        )

    def test_no_positive_marker_falls_back_to_self_name(self):
        self.assertEqual(_detected_recorder("Reviewer", {}), "Reviewer")


class ReviewLedgerTests(unittest.TestCase):
    """The review ledger + check is the brain of the push gate: a commit is approved
    only if a PEER (not the pusher) recorded an approving verdict (SHIP/GO) for that
    exact SHA, and bypass entries never count as approval."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 0}))

    def _record(self, *args, env=None):
        run_env = os.environ.copy()
        for key in list(run_env):
            if key.startswith("CODEX_"):
                run_env.pop(key, None)
        run_env.pop("CLAUDECODE", None)
        run_env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        run_env.pop("CLAUDE_CODE", None)
        run_env.pop("CLAUDE_SESSION_ID", None)
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, str(REVIEW), "record", "--project", self.tmp, *args],
            capture_output=True, text=True, timeout=20, env=run_env)

    def _check(self, sha, exclude):
        return subprocess.run(
            [sys.executable, str(REVIEW), "check", "--project", self.tmp,
             "--sha", sha, "--exclude", exclude],
            capture_output=True, text=True, timeout=20)

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", self.tmp, *args],
            capture_output=True, text=True, timeout=20, check=True)

    def test_peer_ship_approves_the_sha(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "SHIP")
        self.assertEqual(self._check("abc123", "Claude").returncode, 0)

    def test_unknown_sha_is_not_approved(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "SHIP")
        self.assertNotEqual(self._check("other", "Claude").returncode, 0)

    def test_rejecting_verdict_is_not_approval(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "FIX-FIRST")
        self.assertNotEqual(self._check("abc123", "Claude").returncode, 0)

    def test_self_review_is_excluded(self):
        # the pusher cannot approve their own push
        self._record("--self", "Claude", "--sha", "abc123", "--verdict", "SHIP")
        self.assertNotEqual(self._check("abc123", "Claude").returncode, 0)

    def test_go_verdict_also_approves(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "GO")
        self.assertEqual(self._check("abc123", "Claude").returncode, 0)

    def test_recording_short_sha_canonicalizes_to_full_git_sha(self):
        self._git("init", "-q")
        self._git("config", "user.email", "codex@example.test")
        self._git("config", "user.name", "Codex")
        (pathlib.Path(self.tmp) / "work.txt").write_text("reviewed\n")
        self._git("add", "work.txt")
        self._git("commit", "-q", "-m", "reviewed")
        full_sha = self._git("rev-parse", "HEAD").stdout.strip()
        short_sha = self._git("rev-parse", "--short=7", "HEAD").stdout.strip()

        r = self._record("--self", "Codex", "--sha", short_sha, "--verdict", "SHIP")
        led = json.loads((self.collab / "collaboration_reviews.json").read_text())

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(short_sha, full_sha)
        self.assertEqual(led["reviews"][0]["sha"], full_sha)
        self.assertEqual(self._check(full_sha, "Claude").returncode, 0)

    def test_bypass_entry_is_not_an_approval(self):
        self._record("--self", "Claude", "--sha", "abc123", "--verdict", "BYPASS",
                     "--bypass")
        self.assertNotEqual(self._check("abc123", "Claude").returncode, 0)

    def test_ledger_records_are_appended_not_clobbered(self):
        self._record("--self", "Codex", "--sha", "s1", "--verdict", "SHIP")
        self._record("--self", "Codex", "--sha", "s2", "--verdict", "SHIP")
        led = json.loads((self.collab / "collaboration_reviews.json").read_text())
        shas = {e["sha"] for e in led["reviews"]}
        self.assertEqual(shas, {"s1", "s2"})

    def test_recorded_by_is_written_and_must_match_reviewer(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "SHIP",
                     env={"CODEX_CI": "1"})
        led = json.loads((self.collab / "collaboration_reviews.json").read_text())

        self.assertEqual(led["reviews"][0]["recorded_by"], "Codex")
        self.assertEqual(self._check("abc123", "Claude").returncode, 0)

    def test_codex_environment_cannot_record_as_claude(self):
        r = self._record("--self", "Claude", "--sha", "abc123", "--verdict", "SHIP",
                         env={"CODEX_CI": "1"})

        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ACTOR_MISMATCH", r.stderr)
        self.assertNotEqual(self._check("abc123", "Codex").returncode, 0)

    def test_claude_code_environment_can_record_as_claude(self):
        r = self._record("--self", "Claude", "--sha", "abc123", "--verdict", "SHIP",
                         env={"CLAUDE_CODE_ENTRYPOINT": "cli"})
        led = json.loads((self.collab / "collaboration_reviews.json").read_text())

        self.assertEqual(r.returncode, 0)
        self.assertEqual(led["reviews"][0]["recorded_by"], "Claude")
        self.assertEqual(self._check("abc123", "Codex").returncode, 0)

    def test_mismatched_recorded_by_does_not_approve(self):
        (self.collab / "collaboration_reviews.json").write_text(json.dumps({
            "reviews": [{
                "reviewer": "Claude",
                "recorded_by": "Codex",
                "sha": "abc123",
                "verdict": "SHIP",
                "bypass": False,
            }]
        }))

        self.assertNotEqual(self._check("abc123", "Codex").returncode, 0)

    def test_legacy_entry_without_recorded_by_still_approves(self):
        (self.collab / "collaboration_reviews.json").write_text(json.dumps({
            "reviews": [{
                "reviewer": "Claude",
                "sha": "abc123",
                "verdict": "SHIP",
                "bypass": False,
            }]
        }))

        self.assertEqual(self._check("abc123", "Codex").returncode, 0)

    def test_verdict_is_case_insensitive(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "ship")
        self.assertEqual(self._check("abc123", "Claude").returncode, 0)


class LatestVerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.mkdir(os.path.join(self.tmp, ".collab"))

    def test_returns_most_recent_verdict_for_sha(self):
        import _review
        reviewer = _review._detected_recorder("Codex")
        _review.record(self.tmp, reviewer, "deadbeef", "FIX-FIRST", note="first")
        _review.record(self.tmp, reviewer, "deadbeef", "GO", note="now ok")
        v = _review.latest_verdict(self.tmp, "deadbeef")
        self.assertEqual(v["verdict"], "GO")
        self.assertEqual(v["note"], "now ok")

    def test_unknown_sha_is_none(self):
        import _review
        self.assertEqual(_review.latest_verdict(self.tmp, "nope")["verdict"], "NONE")


if __name__ == "__main__":
    unittest.main()
