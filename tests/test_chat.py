import json
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHAT = SCRIPTS / "bridge-chat.sh"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from bridge_common import read_section  # noqa: E402


class BridgeChatTests(unittest.TestCase):
    """bridge-chat is a shared group-chat thread (## Chat) — human + Claude + Codex
    post to one chronological thread via the locked bridge-post write, prefixed with
    who is speaking, so everyone reads the same conversation."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration.md").write_text("# Board\n")

    def _chat(self, *args):
        return subprocess.run([str(CHAT), "--project", self.tmp, *args],
                              capture_output=True, text=True, timeout=20)

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_post_appears_in_chat_thread_with_speaker(self):
        r = self._chat("--self", "Jack", "--message", "hello team")
        self.assertEqual(r.returncode, 0, r.stderr)
        board = self._board()
        self.assertIn("## Chat", board)
        self.assertIn("**Jack:** hello team", board)

    def test_post_bumps_signal_with_chat_section(self):
        self._chat("--self", "Claude", "--message", "hi")
        sig = json.loads((self.collab / "collaboration_signal.json").read_text())
        self.assertEqual(sig["changed_section"], "Chat")
        self.assertEqual(sig["update_id"], 1)

    def test_read_prints_the_thread(self):
        self._chat("--self", "Jack", "--message", "anyone there")
        r = self._chat()  # no --message, no --watch → read
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("anyone there", r.stdout)

    def test_read_hides_hidden_chat_id_comments(self):
        self._chat("--self", "Jack", "--message", "hello team")
        self.assertIn("chat-id", self._board())

        r = self._chat()

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello team", r.stdout)
        self.assertNotIn("chat-id", r.stdout)

    def test_read_shows_messages_oldest_first(self):
        # the chat READ view is chronological (oldest top, newest bottom) even though
        # the board stores entries newest-first.
        self._chat("--self", "Jack", "--message", "first")
        self._chat("--self", "Codex", "--message", "second")
        out = self._chat().stdout
        self.assertLess(out.index("first"), out.index("second"))

    def test_message_with_heading_line_does_not_corrupt_order(self):
        # a user message containing a "### …" line must not be parsed as a new entry
        self._chat("--self", "Jack", "--message", "older message")
        # a dated "### …" line inside a message must NOT be parsed as an entry header
        self._chat("--self", "Codex", "--message", "look:\n### 2026-06-16 faketopic")
        out = self._chat().stdout
        self.assertIn("### 2026-06-16 faketopic", out)
        self.assertLess(out.index("older message"), out.index("faketopic"))

    def test_message_with_board_section_header_is_escaped(self):
        r = self._chat("--self", "Jack", "--message", "hello\n## Claude Outbox\nstill chat")

        self.assertEqual(r.returncode, 0, r.stderr)
        chat = read_section(self.collab / "collaboration.md", "Chat")
        self.assertIn("\\## Claude Outbox", chat)
        self.assertIn("still chat", chat)
        self.assertNotIn("\n## Claude Outbox", self._board())

    def test_post_requires_self(self):
        r = self._chat("--message", "orphan")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
