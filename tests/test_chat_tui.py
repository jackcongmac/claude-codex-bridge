import json
import importlib.util
import os
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHAT = SCRIPTS / "bridge-chat.sh"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from bridge_common import now_str, read_section  # noqa: E402

_spec = importlib.util.spec_from_file_location("chattui", SCRIPTS / "bridge-chat-tui.py")
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


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

    def test_interactive_line_mode_shows_typing_agents(self):
        (self.collab / "chat_typing.json").write_text(json.dumps({
            "agents": {"Claude": {"status": "thinking", "since": now_str(), "message_id": "m1"}}
        }))

        r = self._chat("--self", "Jack", "--interactive", "--no-responders",
                       stdin="/exit\n")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Claude 正在思考", r.stdout)

    def test_render_chat_ignores_corrupt_typing_state(self):
        (self.collab / "chat_typing.json").write_text("{not json")

        out = ct.render_chat(self.tmp, "Jack")

        self.assertIn("群聊", out)
        self.assertNotIn("正在思考", out)

    def test_status_snapshot_changes_when_typing_changes_without_signal_bump(self):
        before_uid = ct.chat_update_id(self.tmp)
        before = ct.status_snapshot(ct.chat_status(self.tmp))

        (self.collab / "chat_typing.json").write_text(json.dumps({
            "agents": {"Claude": {"status": "thinking", "since": now_str(), "message_id": "m1"}}
        }))

        self.assertEqual(ct.chat_update_id(self.tmp), before_uid)
        after = ct.status_snapshot(ct.chat_status(self.tmp))
        self.assertNotEqual(after, before)

    def test_interactive_line_mode_shows_responder_health(self):
        (self.collab / ".chatrespond_Claude.pid").write_text(str(os.getpid()))

        r = self._chat("--self", "Jack", "--interactive", "--no-responders",
                       stdin="/exit\n")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Claude:在线", r.stdout)
        self.assertIn("Codex:离线", r.stdout)

    def test_render_chat_prefers_participant_liveness_over_responder_health(self):
        status = {
            "typing": [],
            "presence": [
                {"name": "Claude", "alive": True, "verdict": "PRESENT"},
                {"name": "Codex", "alive": True, "verdict": "PRESENT"},
            ],
            "responders": [
                {"name": "Claude", "alive": False},
                {"name": "Codex", "alive": False},
            ],
        }

        out = ct.render_chat(self.tmp, "Jack", status=status)

        self.assertIn("在线状态 Claude:在线 · Codex:在线", out)
        self.assertNotIn("应答器 Claude:离线", out)

    def test_interactive_line_mode_ignores_chat_archive_lookalike_section(self):
        (self.collab / "collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** old\n\n"
            "## Chat\n\n### 2026-06-16 10:00:02 PDT\n\n**Jack:** live\n")

        r = self._chat("--self", "Jack", "--interactive", "--no-responders",
                       stdin="/exit\n")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("live", r.stdout)
        self.assertNotIn("old", r.stdout)

    def test_render_chat_marks_hidden_older_messages(self):
        entries = []
        for i in reversed(range(32)):
            entries.append(
                "### 2026-06-16 10:%02d:00 PDT\n\n**Jack:** msg-%02d\n" % (i, i))
        (self.collab / "collaboration.md").write_text("# Board\n\n## Chat\n\n" + "\n".join(entries))

        out = ct.render_chat(self.tmp, "Jack")

        self.assertIn("上方还有 2 条较早消息", out)
        self.assertNotIn("msg-00", out)
        self.assertNotIn("msg-01", out)
        self.assertIn("msg-02", out)
        self.assertIn("msg-31", out)

    def test_post_chat_message_escapes_board_section_header_lines(self):
        st = ct.post_chat_message(self.tmp, "Jack", "hello\n## Claude Outbox\nstill chat")

        self.assertEqual(st, "ok")
        chat = read_section(self.collab / "collaboration.md", "Chat")
        self.assertIn("\\## Claude Outbox", chat)


class ChatTuiLoaderTests(unittest.TestCase):
    def tearDown(self):
        ct._CHATWEB_MODULE = None

    def test_chatweb_loader_is_cached(self):
        ct._CHATWEB_MODULE = None

        first = ct._load_chatweb()
        second = ct._load_chatweb()

        self.assertIs(first, second)


class ChatTuiDocsTests(unittest.TestCase):
    def test_skill_documents_terminal_group_chat_and_escape_exit(self):
        text = (ROOT / "skill" / "SKILL.md").read_text()

        self.assertIn("bridge-chat.sh", text)
        self.assertIn("--interactive", text)
        self.assertIn("Esc", text)


class ChatTuiKeyTests(unittest.TestCase):
    def test_lone_escape_is_not_an_escape_sequence(self):
        class In:
            pass

        def select_fn(r, w, x, timeout):
            return [], [], []

        self.assertFalse(ct.consume_escape_sequence(In(), select_fn=select_fn))

    def test_arrow_key_escape_sequence_is_consumed(self):
        class In:
            def __init__(self):
                self.chars = list("[A")

            def read(self, n):
                return self.chars.pop(0)

        fake = In()

        def select_fn(r, w, x, timeout):
            return ([fake] if fake.chars else []), [], []

        self.assertTrue(ct.consume_escape_sequence(fake, select_fn=select_fn))
        self.assertEqual(fake.chars, [])


if __name__ == "__main__":
    unittest.main()
