import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from bridge_common import read_section  # noqa: E402

_spec = importlib.util.spec_from_file_location("chatweb", SCRIPTS / "bridge-chat-web.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class ParseChatTests(unittest.TestCase):
    def test_empty_section_is_no_messages(self):
        self.assertEqual(cw.parse_chat(""), [])

    def test_parses_speaker_and_text_chronologically(self):
        # board stores newest-first; parse returns oldest-first
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:02 PDT\n\n**Codex:** second\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** first\n")
        msgs = cw.parse_chat(section)
        self.assertEqual([(m["speaker"], m["text"]) for m in msgs],
                         [("Jack", "first"), ("Codex", "second")])

    def test_message_with_inner_heading_is_not_split(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** look:\n### 2026-06-16 note\n")
        msgs = cw.parse_chat(section)
        self.assertEqual(len(msgs), 1)
        self.assertIn("### 2026-06-16 note", msgs[0]["text"])


class ServerRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = pathlib.Path(self.tmp) / ".collab"
        collab.mkdir()
        (collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (collab / "collaboration.md").write_text("# Board\n")
        self.httpd, self.port = cw.make_server(self.tmp, "Jack", 0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        self.httpd.shutdown()

    def _get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=5).read().decode()

    def _post(self, path, data):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Token": self.httpd.token})
        return urllib.request.urlopen(req, timeout=5).read().decode()

    def test_index_serves_html(self):
        self.assertIn("<html", self._get("/").lower())

    def test_send_without_token_is_forbidden(self):
        req = urllib.request.Request(self.base + "/send",
                                     data=json.dumps({"text": "x"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_self_name_is_html_escaped(self):
        httpd, port = cw.make_server(self.tmp, "<b>x</b>", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            page = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read().decode()
            self.assertNotIn("<b>x</b>", page)
            self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        finally:
            httpd.shutdown()

    def test_send_then_messages_roundtrip(self):
        self._post("/send", {"text": "hello from the web"})
        time.sleep(0.2)
        msgs = json.loads(self._get("/messages"))
        self.assertTrue(any(m["speaker"] == "Jack" and "hello from the web" in m["text"]
                            for m in msgs))

    def test_send_escapes_board_section_header_lines(self):
        self._post("/send", {"text": "hello\n## Claude Outbox\nstill chat"})
        time.sleep(0.2)

        board = pathlib.Path(self.tmp, ".collab", "collaboration.md")
        chat = read_section(board, "Chat")
        msgs = json.loads(self._get("/messages"))

        self.assertIn("\\## Claude Outbox", chat)
        self.assertEqual(msgs[-1]["text"], "hello\n\\## Claude Outbox\nstill chat")

    def test_messages_endpoint_ignores_chat_archive_lookalike_section(self):
        pathlib.Path(self.tmp, ".collab", "collaboration.md").write_text(
            "# Board\n\n## Chat Archive\n\n### 2026-06-16 10:00:01 PDT\n\n**Jack:** old\n\n"
            "## Chat\n\n### 2026-06-16 10:00:02 PDT\n\n**Jack:** live\n")

        msgs = json.loads(self._get("/messages"))

        self.assertEqual([(m["speaker"], m["text"]) for m in msgs], [("Jack", "live")])

    def test_status_reports_typing_agents(self):
        pathlib.Path(self.tmp, ".collab", "chat_typing.json").write_text(json.dumps({
            "agents": {"Claude": {"status": "thinking", "since": cw.now_str(), "message_id": "abc"}}
        }))

        status = json.loads(self._get("/status"))

        self.assertEqual(status["typing"], ["Claude"])

    def test_status_reports_responder_health(self):
        pathlib.Path(self.tmp, ".collab", ".chatrespond_Claude.pid").write_text(str(os.getpid()))

        status = json.loads(self._get("/status"))

        self.assertEqual(status["responders"], [
            {"name": "Claude", "alive": True},
            {"name": "Codex", "alive": False},
        ])

    def test_index_polls_status_for_typing_indicator(self):
        page = self._get("/")

        self.assertIn("id=typing", page)
        self.assertIn("id=presence", page)
        self.assertIn("/status", page)


if __name__ == "__main__":
    unittest.main()
