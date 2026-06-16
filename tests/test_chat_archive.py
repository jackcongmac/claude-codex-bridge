import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_spec = importlib.util.spec_from_file_location("chatweb", SCRIPTS / "bridge-chat-web.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class ChatArchiveTests(unittest.TestCase):
    """Closing a chat session archives the full ## Chat thread to disk and clears the
    live thread, so past conversations aren't lost and the next session starts fresh."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Decision Log\n\nkeep me\n\n"
            "## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** hi\n\n"
            "### 2026-06-16 10:00:02 PDT\n\n**Claude:** hello\n")

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_archive_writes_a_file_with_the_thread(self):
        path = cw.archive_and_clear_chat(self.tmp)
        self.assertIsNotNone(path)
        self.assertTrue(pathlib.Path(path).exists())
        text = pathlib.Path(path).read_text()
        self.assertIn("Jack", text)
        self.assertIn("hello", text)

    def test_archive_clears_the_chat_section_but_keeps_others(self):
        cw.archive_and_clear_chat(self.tmp)
        board = self._board()
        self.assertNotIn("**Jack:** hi", board)
        self.assertNotIn("## Chat", board)
        self.assertIn("## Decision Log", board)   # other sections untouched
        self.assertIn("keep me", board)

    def test_archive_is_chronological(self):
        path = cw.archive_and_clear_chat(self.tmp)
        text = pathlib.Path(path).read_text()
        self.assertLess(text.index("hi"), text.index("hello"))

    def test_does_not_match_a_similarly_named_section(self):
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\nold stuff\n\n"
            "## Chat\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** hi\n")
        cw.archive_and_clear_chat(self.tmp)
        board = self._board()
        self.assertIn("## Chat Archive", board)   # the look-alike section survives
        self.assertNotIn("**Jack:** hi", board)

    def test_empty_chat_archives_nothing(self):
        (self.collab / "collaboration.md").write_text("# Board\n")
        self.assertIsNone(cw.archive_and_clear_chat(self.tmp))


class SkillTriggerTests(unittest.TestCase):
    def test_skill_documents_the_group_chat_trigger(self):
        text = (ROOT / "skill" / "SKILL.md").read_text()
        self.assertIn("bridge-chat-web", text)
        self.assertIn("群聊", text)
        self.assertIn("group chat", text.lower())


if __name__ == "__main__":
    unittest.main()
