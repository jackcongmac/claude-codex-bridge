import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LIVE = SCRIPTS / "bridge-live.sh"


class BridgeLiveTests(unittest.TestCase):
    """`bridge-live` is the one-command "go live": register (locked) + start/supervise
    presence-keepalive + report liveness, and print the board-wait ARM line the agent
    must run itself (board-wait's exit is the agent's wake signal — a supervisor can't
    own it without swallowing the wake)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        subprocess.run([str(SCRIPTS / "init-collaboration.sh"), self.tmp],
                       capture_output=True, text=True)
        self.collab = pathlib.Path(self.tmp) / ".collab"

    def _kill_keepalive(self):
        pf = self.collab / ".keepalive_Claude.pid"
        if pf.exists():
            try:
                os.kill(int(pf.read_text().strip()), signal.SIGTERM)
            except Exception:
                pass

    def _parts(self):
        return json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]

    def _live(self):
        return subprocess.run(
            [str(LIVE), "--self", "Claude", "--project", self.tmp,
             "--keepalive-interval", "2"],
            capture_output=True, text=True, timeout=20)

    def test_go_live_registers_starts_keepalive_and_reports(self):
        r = self._live()
        try:
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Claude", {a["name"] for a in self._parts()})
            time.sleep(1)
            pf = self.collab / ".keepalive_Claude.pid"
            self.assertTrue(pf.exists(), "keepalive pidfile should exist")
            # liveness report is printed for self
            self.assertIn("Claude", r.stdout)
            # it must hand the agent the board-wait ARM line (it can't own that itself)
            self.assertIn("board-wait", r.stdout)
        finally:
            self._kill_keepalive()

    def test_go_live_is_idempotent_single_keepalive(self):
        try:
            self._live()
            time.sleep(0.5)
            pid1 = (self.collab / ".keepalive_Claude.pid").read_text().strip()
            self._live()
            time.sleep(0.5)
            pid2 = (self.collab / ".keepalive_Claude.pid").read_text().strip()
            self.assertEqual(pid1, pid2, "re-running must not start a 2nd keepalive")
        finally:
            self._kill_keepalive()

    def test_readme_documents_bridge_live(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("scripts/bridge-live.sh", readme)
        self.assertIn("presence-keepalive", readme)
        self.assertIn("board-wait", readme)


if __name__ == "__main__":
    unittest.main()
