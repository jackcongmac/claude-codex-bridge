import importlib.util
import json
import os
import pathlib
import socket
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

    def test_format_chat_message_adds_hidden_stable_id(self):
        body = cw.format_chat_message("Jack", "hello", msg_id="m1")

        self.assertIn("<!-- chat-id:m1 -->", body)
        self.assertIn("**Jack:** hello", body)

    def test_parse_chat_extracts_hidden_id_without_showing_it(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            "<!-- chat-id:m1 -->\n**Jack:** hello\n")
        msgs = cw.parse_chat(section)

        self.assertEqual(msgs[0]["_id"], "m1")
        self.assertEqual(msgs[0]["text"], "hello")

    def test_parse_chat_preserves_worker_speaker_and_nonce(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            "<!-- chat-id:m1 -->\n**Codex (worker a1b2c3):** ran tests\n")

        msgs = cw.parse_chat(section)

        self.assertEqual(msgs[0]["speaker"], "Codex (worker a1b2c3)")
        self.assertEqual(msgs[0]["speaker_base"], "Codex")
        self.assertEqual(msgs[0]["worker_nonce"], "a1b2c3")
        self.assertEqual(msgs[0]["text"], "ran tests")

    def test_message_with_inner_heading_is_not_split(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** look:\n### 2026-06-16 note\n")
        msgs = cw.parse_chat(section)
        self.assertEqual(len(msgs), 1)
        self.assertIn("### 2026-06-16 note", msgs[0]["text"])

    def test_duplicate_messages_get_stable_ordinals(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude repeat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n**Jack:** @Claude repeat\n")
        msgs = cw.parse_chat(section)

        self.assertEqual([m.get("_dup") for m in msgs], [0, 1])

    def test_duplicate_messages_with_ids_do_not_need_ordinals(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m2 -->\n**Jack:** @Claude repeat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m1 -->\n**Jack:** @Claude repeat\n")
        msgs = cw.parse_chat(section)

        self.assertEqual([m.get("_id") for m in msgs], ["m1", "m2"])
        self.assertEqual([m.get("_dup") for m in msgs], [None, None])

    def test_format_chat_message_embeds_sent_at_and_trigger(self):
        body = cw.format_chat_message("Jack", "hi", msg_id="m1",
                                      sent_at="2026-06-29T11:40:05", send_trigger="enter")
        self.assertIn("chat-id:m1", body)
        self.assertIn("sent_at:2026-06-29T11:40:05", body)
        self.assertIn("trigger:enter", body)
        self.assertIn("**Jack:** hi", body)

    def test_parse_chat_extracts_sent_at_and_trigger(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            "<!-- chat-id:m1 sent_at:2026-06-29T11:40:05 trigger:enter -->\n"
            "**Jack:** hi\n")
        msgs = cw.parse_chat(section)
        self.assertEqual(msgs[0]["_id"], "m1")
        self.assertEqual(msgs[0]["sent_at"], "2026-06-29T11:40:05")
        self.assertEqual(msgs[0]["send_trigger"], "enter")
        self.assertEqual(msgs[0]["text"], "hi")

    def test_parse_chat_without_metadata_has_no_sent_at(self):
        # backward compatibility: old messages (chat-id only) still parse cleanly
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n<!-- chat-id:m1 -->\n**Jack:** hi\n")
        msgs = cw.parse_chat(section)
        self.assertEqual(msgs[0]["_id"], "m1")
        self.assertEqual(msgs[0]["text"], "hi")
        self.assertIsNone(msgs[0].get("sent_at"))
        self.assertIsNone(msgs[0].get("send_trigger"))

    def test_format_chat_message_embeds_image_ref(self):
        body = cw.format_chat_message("Jack", "", msg_id="m1", img="a1b2c3d4e5f6.png")
        self.assertIn("chat-id:m1", body)
        self.assertIn("img:a1b2c3d4e5f6.png", body)

    def test_parse_chat_extracts_image_ref(self):
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            "<!-- chat-id:m1 img:a1b2c3d4e5f6.png -->\n**Jack:** look\n")
        msgs = cw.parse_chat(section)
        self.assertEqual(msgs[0]["img"], "a1b2c3d4e5f6.png")
        self.assertEqual(msgs[0]["text"], "look")

    def test_parse_chat_drops_malformed_image_ref(self):
        # an image ref that isn't a safe <hex>.<ext> must not survive parsing
        section = (
            "## Chat\n\n"
            "### 2026-06-16 10:00:01 PDT\n\n"
            "<!-- chat-id:m1 img:../etc/passwd -->\n**Jack:** x\n")
        msgs = cw.parse_chat(section)
        self.assertIsNone(msgs[0].get("img"))


class ServerRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = pathlib.Path(self.tmp) / ".collab"
        collab.mkdir()
        (collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (collab / "collaboration.md").write_text("# Board\n")
        self.httpd, self.port = cw.make_server(self.tmp, "Jack", 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)

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
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            page = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read().decode()
            self.assertNotIn("<b>x</b>", page)
            self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=1)

    def test_send_then_messages_roundtrip(self):
        self._post("/send", {"text": "hello from the web"})
        time.sleep(0.2)
        msgs = json.loads(self._get("/messages"))
        self.assertTrue(any(m["speaker"] == "Jack" and "hello from the web" in m["text"]
                            for m in msgs))

    def test_send_persists_sent_at_and_trigger(self):
        self._post("/send", {"text": "timed", "sent_at": "2026-06-29T11:40:05",
                             "send_trigger": "enter"})
        time.sleep(0.2)
        msgs = json.loads(self._get("/messages"))
        m = next(m for m in msgs if "timed" in m["text"])
        self.assertEqual(m["sent_at"], "2026-06-29T11:40:05")
        self.assertEqual(m["send_trigger"], "enter")

    def test_send_drops_invalid_trigger_and_sent_at(self):
        # untrusted client values must be whitelisted/format-checked, not stored raw
        self._post("/send", {"text": "bad meta", "sent_at": "not-a-time; rm -rf",
                             "send_trigger": "evil-->x"})
        time.sleep(0.2)
        msgs = json.loads(self._get("/messages"))
        m = next(m for m in msgs if "bad meta" in m["text"])
        self.assertIsNone(m.get("sent_at"))
        self.assertIsNone(m.get("send_trigger"))

    def test_index_captures_send_trigger_and_sent_at(self):
        page = self._get("/")
        self.assertIn("send_trigger", page)
        self.assertIn("sent_at", page)

    def test_index_renders_message_time(self):
        page = self._get("/")
        self.assertIn("class=time", page)

    _PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32)

    def _post_bytes(self, path, raw, ctype, token=True):
        headers = {"Content-Type": ctype}
        if token:
            headers["X-Token"] = self.httpd.token
        req = urllib.request.Request(self.base + path, data=raw, headers=headers)
        return urllib.request.urlopen(req, timeout=5)

    def test_upload_then_serve_roundtrip(self):
        resp = json.loads(self._post_bytes("/upload", self._PNG, "image/png").read().decode())
        self.assertTrue(resp["ok"])
        ref = resp["id"]
        self.assertRegex(ref, r"^[0-9a-f]{8,}\.png$")
        served = self._post_bytes  # noqa
        got = urllib.request.urlopen(self.base + "/uploads/" + ref, timeout=5)
        self.assertEqual(got.read(), self._PNG)
        self.assertEqual(got.headers.get("Content-Type").split(";")[0], "image/png")

    def test_upload_without_token_is_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_bytes("/upload", self._PNG, "image/png", token=False)
        self.assertEqual(cm.exception.code, 403)

    def test_upload_rejects_non_image(self):
        resp = json.loads(
            self._post_bytes("/upload", b"<script>not an image", "image/png").read().decode())
        self.assertFalse(resp["ok"])

    def test_serve_unknown_or_bad_upload_id_is_404(self):
        for bad in ("/uploads/deadbeefdeadbeef.png", "/uploads/..%2fetc%2fpasswd", "/uploads/x"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + bad, timeout=5)
            self.assertEqual(cm.exception.code, 404)

    def test_send_with_image_ref_persists(self):
        self._post("/send", {"text": "", "img": "a1b2c3d4e5f6.png"})
        time.sleep(0.2)
        msgs = json.loads(self._get("/messages"))
        self.assertTrue(any(m.get("img") == "a1b2c3d4e5f6.png" for m in msgs))

    def test_index_has_image_upload_wiring(self):
        page = self._get("/")
        self.assertIn("/upload", page)
        self.assertIn("paste", page)
        self.assertIn("drop", page)

    def test_send_escapes_board_section_header_lines(self):
        self._post("/send", {"text": "hello\n## Claude Outbox\nstill chat"})
        time.sleep(0.2)

        board = pathlib.Path(self.tmp, ".collab", "collaboration.md")
        chat = read_section(board, "Chat")
        msgs = json.loads(self._get("/messages"))

        self.assertIn("\\## Claude Outbox", chat)
        self.assertEqual(msgs[-1]["text"], "hello\n\\## Claude Outbox\nstill chat")

    def test_quit_reports_archive_failure_without_shutdown(self):
        orig = cw.archive_and_clear_chat
        cw.archive_and_clear_chat = lambda project: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            req = urllib.request.Request(
                self.base + "/quit", data=b"{}",
                headers={"Content-Type": "application/json", "X-Token": self.httpd.token})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            body = json.loads(cm.exception.read().decode())

            self.assertEqual(cm.exception.code, 500)
            self.assertEqual(body, {"ok": False, "error": "archive_failed"})
            self.assertIn("<html", self._get("/").lower())
        finally:
            cw.archive_and_clear_chat = orig

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

    def test_status_ignores_corrupt_typing_state(self):
        pathlib.Path(self.tmp, ".collab", "chat_typing.json").write_text("{not json")

        status = json.loads(self._get("/status"))

        self.assertEqual(status["typing"], [])
        self.assertEqual([r["name"] for r in status["responders"]], ["Claude", "Codex"])

    def test_status_ignores_malformed_typing_agents_state(self):
        pathlib.Path(self.tmp, ".collab", "chat_typing.json").write_text(json.dumps({
            "agents": ["not", "a", "mapping"]
        }))

        status = json.loads(self._get("/status"))

        self.assertEqual(status["typing"], [])
        self.assertEqual([r["name"] for r in status["responders"]], ["Claude", "Codex"])

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

    def test_index_guards_enter_send_for_ime_composition(self):
        page = self._get("/")

        self.assertIn("isComposing", page)
        self.assertIn("229", page)


class ServerStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = pathlib.Path(self.tmp) / ".collab"
        collab.mkdir()
        (collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (collab / "collaboration.md").write_text("# Board\n")

    def _busy_port(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return sock, sock.getsockname()[1]

    def test_default_server_falls_back_when_preferred_port_is_busy(self):
        sock, busy_port = self._busy_port()
        try:
            httpd, port = cw.make_server_with_default_fallback(self.tmp, "Jack", busy_port)
            try:
                self.assertNotEqual(port, busy_port)
            finally:
                httpd.server_close()
        finally:
            sock.close()

    def test_strict_server_keeps_explicit_busy_port_failure(self):
        sock, busy_port = self._busy_port()
        try:
            with self.assertRaises(OSError):
                cw.make_server(self.tmp, "Jack", busy_port)
        finally:
            sock.close()


class ServerLifecycleTests(unittest.TestCase):
    def test_serve_chat_closes_server_after_shutdown(self):
        class Server:
            def __init__(self):
                self.closed = False
                self.served = False

            def serve_forever(self):
                self.served = True

            def server_close(self):
                self.closed = True

        server = Server()
        calls = []

        status = cw.serve_chat(
            server, responders=["r"], supervisor_stop="stop", supervisor="supervisor",
            supervisor_interval=3, shutdown=lambda *args, **kwargs: calls.append((args, kwargs)))

        self.assertEqual(status, 0)
        self.assertTrue(server.served)
        self.assertTrue(server.closed)
        self.assertEqual(calls, [((["r"], "stop", "supervisor"), {"join_timeout": 5})])


if __name__ == "__main__":
    unittest.main()
