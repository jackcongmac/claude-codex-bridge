import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
POST = SCRIPTS / "_post.py"
BRIDGE_POST = SCRIPTS / "bridge-post.sh"
sys.path.insert(0, str(SCRIPTS))
import bridge_common as bc  # noqa: E402


class PostTransactionTests(unittest.TestCase):
    """_post.py is the transactional manual board write: under collaboration.lock it
    appends to the caller's outbox AND bumps the signal with the full schema, so a
    manual post can't lose updates or leave a stale/empty signal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 4, "updated_by": "none"}))
        (self.collab / "collaboration.md").write_text("# Board\n")

    def _post(self, *args):
        return subprocess.run(
            [sys.executable, str(POST), "post", "--project", self.tmp, *args],
            capture_output=True, text=True, timeout=20)

    def _bridge_post(self, *args):
        return subprocess.run(
            [str(BRIDGE_POST), "--project", self.tmp, *args],
            capture_output=True, text=True, timeout=20)

    def _signal(self):
        return json.loads((self.collab / "collaboration_signal.json").read_text())

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_guard_aborts_write_atomically(self):
        from _post import post
        # guard returns False => nothing written, signal untouched
        st = post(self.tmp, "Claude", "blocked", section="Chat", guard=lambda board: False)
        self.assertEqual(st, "superseded")
        self.assertNotIn("blocked", self._board())
        self.assertEqual(self._signal()["update_id"], 4)
        # guard returns True => normal append + bump
        st = post(self.tmp, "Claude", "allowed", section="Chat", guard=lambda board: True)
        self.assertEqual(st, "ok")
        self.assertIn("allowed", self._board())
        self.assertEqual(self._signal()["update_id"], 5)

    def test_post_appends_to_outbox_and_bumps_signal(self):
        r = self._post("--self", "Claude", "--message", "hello world")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello world", self._board())
        self.assertIn("## Claude Outbox", self._board())
        s = self._signal()
        self.assertEqual(s["update_id"], 5)
        self.assertEqual(s["updated_by"], "Claude")
        self.assertEqual(s["changed_section"], "Claude Outbox")
        self.assertTrue(s["updated_at"])

    def test_bridge_post_worker_marks_message_without_new_outbox(self):
        plain = self._bridge_post("--self", "Codex", "--message", "plain status")
        worker = self._bridge_post("--self", "Codex", "--message", "worker status", "--worker")

        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(worker.returncode, 0, worker.stderr)
        board = self._board()
        self.assertIn("## Codex Outbox", board)
        self.assertIn("plain status", board)
        self.assertNotRegex(board, r"\*\*Codex \(worker [0-9a-f]{4,6}\):\*\* plain status")
        self.assertRegex(board, r"\*\*Codex \(worker [0-9a-f]{4,6}\):\*\* worker status")
        self.assertNotRegex(board, r"(?m)^## Codex \(worker [0-9a-f]{4,6}\) Outbox$")

    def test_post_to_an_explicit_section(self):
        r = self._post("--self", "Claude", "--message", "decided X",
                       "--section", "Decision Log")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## Decision Log", self._board())
        self.assertEqual(self._signal()["changed_section"], "Decision Log")

    def test_read_section_matches_exact_header_line(self):
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\nold archived stuff\n\n"
            "## Chat  \n\nlive chat\n\n## Claude Outbox\n\nnote\n")

        self.assertEqual(bc.read_section(self.collab / "collaboration.md", "Chat"),
                         "## Chat  \n\nlive chat")
        self.assertEqual(bc.read_section(self.collab / "collaboration.md", "Missing"), "")

    def test_two_posts_increment_update_id_sequentially(self):
        self._post("--self", "Claude", "--message", "one")
        self._post("--self", "Codex", "--message", "two")
        self.assertEqual(self._signal()["update_id"], 6)

    def test_post_uses_summary_when_given(self):
        self._post("--self", "Claude", "--message", "long body",
                   "--summary", "short summary")
        self.assertEqual(self._signal()["summary"], "short summary")

    def test_signal_failure_preserves_board_content_and_is_reported(self):
        # force the signal bump to fail (make the signal path a directory) AFTER the
        # board append — content must survive, and the failure must be reported, not
        # silently "ok". Content-first ordering: never a lost message / phantom wake.
        sig = self.collab / "collaboration_signal.json"
        sig.unlink()
        sig.mkdir()
        r = self._post("--self", "Claude", "--message", "half committed")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("POSTED_NO_SIGNAL", r.stdout + r.stderr)
        self.assertIn("half committed", self._board())

    def test_post_fails_cleanly_when_lock_is_held(self):
        p = bc.collab_paths(self.tmp)
        self.assertTrue(bc.acquire_lock(p["lock"], "test-holder", ttl=30, wait=0))
        try:
            r = self._post("--self", "Claude", "--message", "blocked", "--wait", "1")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("LOCKBUSY", r.stdout + r.stderr)
            # the held-out post must NOT have bumped the signal
            self.assertEqual(self._signal()["update_id"], 4)
        finally:
            bc.release_lock(p["lock"])


if __name__ == "__main__":
    unittest.main()
