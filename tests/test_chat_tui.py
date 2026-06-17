import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHAT = SCRIPTS / "bridge-chat.sh"


class BridgeChatTuiTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration.md").write_text("# Board\n")

    def _chat(self, *args, stdin=""):
        return subprocess.run([str(CHAT), "--project", self.tmp, *args],
                              input=stdin, capture_output=True, text=True, timeout=20)

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_interactive_line_mode_posts_until_exit(self):
        r = self._chat("--self", "Jack", "--interactive", "--no-responders",
                       stdin="hello from tui\n/exit\n")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("群聊", r.stdout)
        self.assertIn("**Jack:** hello from tui", self._board())

    def test_escape_byte_exits_line_mode_without_posting_following_text(self):
        r = self._chat("--self", "Jack", "--interactive", "--no-responders",
                       stdin="\x1bshould not post\n")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("should not post", self._board())


class ChatTuiDocsTests(unittest.TestCase):
    def test_skill_documents_terminal_group_chat_and_escape_exit(self):
        text = (ROOT / "skill" / "SKILL.md").read_text()

        self.assertIn("bridge-chat.sh", text)
        self.assertIn("--interactive", text)
        self.assertIn("Esc", text)


if __name__ == "__main__":
    unittest.main()
