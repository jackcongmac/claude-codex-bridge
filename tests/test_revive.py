import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REVIVE = SCRIPTS / "bridge-revive.sh"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") + " PDT"


class BridgeReviveTests(unittest.TestCase):
    """bridge-revive: self -> re-ensure liveness (keepalive); peer that isn't live ->
    a board nudge + human notify (NO MCP spawn — can't reopen a closed window)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        subprocess.run([str(SCRIPTS / "init-collaboration.sh"), self.tmp],
                       capture_output=True, text=True)
        self.collab = pathlib.Path(self.tmp) / ".collab"

    def _participants(self, parts):
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": parts}))

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def _kill_keepalive(self, who="Claude"):
        pf = self.collab / (".keepalive_%s.pid" % who)
        if pf.exists():
            try:
                os.kill(int(pf.read_text().strip()), signal.SIGTERM)
            except Exception:
                pass

    def _revive(self, *args):
        return subprocess.run(
            [str(REVIVE), "--self", "Claude", "--project", self.tmp,
             "--keepalive-interval", "2", *args],
            capture_output=True, text=True, timeout=25)

    def test_self_revive_ensures_keepalive(self):
        now = time.time()
        self._participants([{"name": "Claude", "last_seen": _stamp(now - 1)}])
        try:
            r = self._revive()
            self.assertEqual(r.returncode, 0, r.stderr)
            time.sleep(1)
            self.assertTrue((self.collab / ".keepalive_Claude.pid").exists())
        finally:
            self._kill_keepalive()

    def test_dead_peer_gets_a_board_nudge(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 5000)}])  # DEAD
        try:
            sig_before = json.loads(
                (self.collab / "collaboration_signal.json").read_text())["update_id"]
            r = self._revive("--peer", "Codex")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Codex", self._board())
            self.assertRegex(self._board(), r"(?i)re-?join|re-?arm|bridge-live")
            sig_after = json.loads(
                (self.collab / "collaboration_signal.json").read_text())["update_id"]
            self.assertGreater(sig_after, sig_before, "nudge should bump the signal")
        finally:
            self._kill_keepalive()

    def test_live_peer_is_not_nudged(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 1)}])  # fresh
        try:
            board_before = self._board()
            r = self._revive("--peer", "Codex")
            self.assertEqual(r.returncode, 0, r.stderr)
            # no nudge appended (board's Codex-mention count unchanged)
            self.assertEqual(self._board().count("re-join"), board_before.count("re-join"))
        finally:
            self._kill_keepalive()


if __name__ == "__main__":
    unittest.main()
