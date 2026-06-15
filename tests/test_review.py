import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REVIEW = SCRIPTS / "_review.py"


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

    def _record(self, *args):
        return subprocess.run(
            [sys.executable, str(REVIEW), "record", "--project", self.tmp, *args],
            capture_output=True, text=True, timeout=20)

    def _check(self, sha, exclude):
        return subprocess.run(
            [sys.executable, str(REVIEW), "check", "--project", self.tmp,
             "--sha", sha, "--exclude", exclude],
            capture_output=True, text=True, timeout=20)

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

    def test_verdict_is_case_insensitive(self):
        self._record("--self", "Codex", "--sha", "abc123", "--verdict", "ship")
        self.assertEqual(self._check("abc123", "Claude").returncode, 0)


if __name__ == "__main__":
    unittest.main()
