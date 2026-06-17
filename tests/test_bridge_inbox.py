import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class BridgeInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        subprocess.run(
            [str(SCRIPTS / "init-collaboration.sh"), self.tmp],
            capture_output=True,
            text=True,
            check=True,
        )
        self.collab = pathlib.Path(self.tmp) / ".collab"

    def _post(self, who, message):
        return subprocess.run(
            [
                str(SCRIPTS / "bridge-post.sh"),
                "--self",
                who,
                "--project",
                self.tmp,
                "--message",
                message,
                "--summary",
                message,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def _inbox(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "bridge-inbox.py"), *args, "--project", self.tmp],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_peer_outbox_is_pending_until_acknowledged(self):
        post = self._post("Claude", "Codex please review update 618")
        self.assertEqual(post.returncode, 0, post.stderr)

        pending = self._inbox("pending", "--self", "Codex")
        self.assertEqual(pending.returncode, 1)
        self.assertIn("ACTION_REQUIRED", pending.stdout)
        self.assertIn("Claude Outbox", pending.stdout)
        self.assertIn("review update 618", pending.stdout)

        ack = self._inbox(
            "ack",
            "--self",
            "Codex",
            "--status",
            "CLAIM",
            "--note",
            "reviewing now",
        )
        self.assertEqual(ack.returncode, 0, ack.stderr)
        self.assertIn("Codex CLAIM", ack.stdout)

        clear = self._inbox("pending", "--self", "Codex")
        self.assertEqual(clear.returncode, 0, clear.stdout)
        self.assertIn("CLEAR", clear.stdout)

        board = (self.collab / "collaboration.md").read_text()
        self.assertIn("## Inbox Acks", board)
        self.assertIn("reviewing now", board)
        self.assertIn("Claude Outbox", board)

        state = json.loads((self.collab / "inbox_ack.json").read_text())
        self.assertEqual(state["Codex<- Claude"]["status"], "CLAIM")
        self.assertEqual(state["Codex<- Claude"]["last_index"], 1)

        signal = json.loads((self.collab / "collaboration_signal.json").read_text())
        self.assertEqual(signal["updated_by"], "Codex")
        self.assertEqual(signal["changed_section"], "Inbox Acks")

    def test_claude_inbox_reads_codex_outbox(self):
        self._post("Codex", "Claude please review local commit")

        pending = self._inbox("pending", "--self", "Claude")
        self.assertEqual(pending.returncode, 1)
        self.assertIn("Codex Outbox", pending.stdout)
        self.assertIn("review local commit", pending.stdout)

    def test_ack_uses_newest_outbox_item_even_though_board_is_newest_first(self):
        self._post("Claude", "first task")
        self._post("Claude", "second task")

        ack = self._inbox(
            "ack",
            "--self",
            "Codex",
            "--status",
            "ACK",
            "--note",
            "caught up",
        )
        self.assertEqual(ack.returncode, 0, ack.stderr)

        state = json.loads((self.collab / "inbox_ack.json").read_text())
        self.assertEqual(state["Codex<- Claude"]["last_index"], 2)
        board = (self.collab / "collaboration.md").read_text()
        self.assertIn("item #2", board)

    def test_custom_agent_requires_peer(self):
        pending = self._inbox("pending", "--self", "codex-exec-1")
        self.assertEqual(pending.returncode, 2)
        self.assertIn("--peer is required", pending.stderr)


if __name__ == "__main__":
    unittest.main()
