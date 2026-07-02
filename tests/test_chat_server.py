import json
import pathlib
import shutil
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _chat_server as chat_server  # noqa: E402


class ChatServerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = pathlib.Path(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_preferred_port_is_deterministic_and_project_scoped(self):
        first = self.root / "project-one"
        second = self.root / "project-two"

        first_port = chat_server.preferred_port(first)
        again = chat_server.preferred_port(str(first))
        second_port = chat_server.preferred_port(second)
        if second_port == first_port:
            for i in range(20):
                candidate = self.root / ("project-other-%d" % i)
                second_port = chat_server.preferred_port(candidate)
                if second_port != first_port:
                    break

        self.assertEqual(first_port, again)
        self.assertNotEqual(first_port, second_port)
        for port in (first_port, again, second_port):
            self.assertGreaterEqual(port, 8765)
            self.assertLessEqual(port, 9764)

    def test_read_write_roundtrip_and_missing_or_corrupt_read(self):
        self.assertIsNone(chat_server.read_server_info(self.root))

        chat_server.write_server_info(self.root, 9001, 123, "2026-07-01T12:00:00")

        self.assertEqual(chat_server.read_server_info(self.root), {
            "pid": 123,
            "port": 9001,
            "url": "http://127.0.0.1:9001",
            "started_at": "2026-07-01T12:00:00",
        })

        pathlib.Path(chat_server.server_info_path(self.root)).write_text("{not json")
        self.assertIsNone(chat_server.read_server_info(self.root))

    def test_two_project_roots_write_independent_server_info(self):
        one = self.root / "one"
        two = self.root / "two"

        chat_server.write_server_info(one, 9001, 111, "2026-07-01T12:00:00")
        chat_server.write_server_info(two, 9002, 222, "2026-07-01T12:01:00")

        self.assertNotEqual(
            chat_server.server_info_path(one),
            chat_server.server_info_path(two),
        )
        self.assertEqual(chat_server.read_server_info(one)["port"], 9001)
        self.assertEqual(chat_server.read_server_info(two)["port"], 9002)
        self.assertEqual(chat_server.read_server_info(one)["pid"], 111)
        self.assertEqual(chat_server.read_server_info(two)["pid"], 222)

    def test_is_running_uses_injected_process_and_port_predicates(self):
        info = {"pid": 123, "port": 9001}

        self.assertTrue(chat_server.is_running(
            info,
            alive=lambda pid: pid == 123,
            port_open=lambda port: port == 9001,
        ))
        self.assertFalse(chat_server.is_running(
            info,
            alive=lambda pid: False,
            port_open=lambda port: True,
        ))
        self.assertFalse(chat_server.is_running(
            info,
            alive=lambda pid: True,
            port_open=lambda port: False,
        ))
        self.assertFalse(chat_server.is_running(
            None,
            alive=lambda pid: True,
            port_open=lambda port: True,
        ))

    def test_clear_server_info_removes_only_matching_pid(self):
        chat_server.write_server_info(self.root, 9001, 123, "2026-07-01T12:00:00")

        chat_server.clear_server_info(self.root, 999)
        self.assertTrue(pathlib.Path(chat_server.server_info_path(self.root)).exists())

        chat_server.clear_server_info(self.root, 123)
        self.assertFalse(pathlib.Path(chat_server.server_info_path(self.root)).exists())


if __name__ == "__main__":
    unittest.main()
