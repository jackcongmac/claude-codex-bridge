import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bridge_inbox  # noqa: E402


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


class InboxSlaEscalationTests(unittest.TestCase):
    """The silent-peer safety net (platform diagnosis 2026-06-17, spec item C): if an
    item sits in my inbox unacked past an SLA, escalate ONCE to ## Liveness so a human is
    pulled in — instead of the peer being silent for hours with nobody noticing. Cold-start
    backlog (items posted before we started watching) is never escalated; escalate-only,
    no auto-assign."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))

    def _ts(self, epoch):
        return time.strftime("%Y-%m-%d %H:%M:%S PDT", time.localtime(epoch))

    def _set_codex_outbox(self, items):  # items: list of (epoch, body)
        blocks = ["### %s\n\n%s\n" % (self._ts(e), b) for e, b in items]
        body = "# Board\n\n## Codex Outbox\n\n" + "\n".join(reversed(blocks)) + "\n"
        (self.collab / "collaboration.md").write_text(body)

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def _uid(self):
        return json.loads((self.collab / "collaboration_signal.json").read_text())["update_id"]

    def test_cold_start_baselines_and_does_not_escalate_backlog(self):
        t0 = 1_000_000
        self._set_codex_outbox([(t0 - 5000, "ancient ACTION_REQUEST")])
        status, _ = bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0)
        self.assertEqual(status, "baselined")
        self.assertNotIn("## Liveness", self._board())
        self.assertEqual(self._uid(), 0)

    def test_new_item_unacked_past_sla_escalates_once(self):
        t0 = 1_000_000
        self._set_codex_outbox([])
        bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0)  # baseline at t0
        self._set_codex_outbox([(t0 + 10, "please review SHA abc123")])
        status, info = bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0 + 700)
        self.assertEqual(status, "escalated")
        self.assertIn("## Liveness", self._board())
        self.assertEqual(self._uid(), 1, "escalation must bump the signal once")
        # idempotent: same unchanged state must not re-escalate
        status2, _ = bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0 + 800)
        self.assertEqual(status2, "already-escalated")
        self.assertEqual(self._uid(), 1, "must not double-escalate the same item")

    def test_ack_clears_pending_so_no_escalation(self):
        t0 = 1_000_000
        self._set_codex_outbox([])
        bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0)
        self._set_codex_outbox([(t0 + 10, "please review SHA abc123")])
        bridge_inbox.ack(self.tmp, "Claude", "CLAIM")
        status, _ = bridge_inbox.escalate(self.tmp, "Claude", sla_seconds=600, now=t0 + 700)
        self.assertEqual(status, "clear")


if __name__ == "__main__":
    unittest.main()
