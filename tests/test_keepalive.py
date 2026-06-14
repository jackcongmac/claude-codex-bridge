import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PRESENCE = SCRIPTS / "_presence.py"
KEEPALIVE = SCRIPTS / "presence-keepalive.sh"

OLD = "2020-01-01 00:00:00 PDT"


class PresenceHeartbeatTests(unittest.TestCase):
    """`_presence.py heartbeat` refreshes ONLY my last_seen — independent of
    board-wait, and WITHOUT the departure scan/broadcast (that stays board-wait's
    job). This is what the keepalive calls on a short interval so last_seen reflects
    'process alive' even mid-turn (kills the false-stale at the source)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 5, "updated_by": "none"}))

    def _participants(self, parts):
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": parts}))

    def _read_parts(self):
        return json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]

    def _heartbeat(self, who):
        return subprocess.run(
            [sys.executable, str(PRESENCE), "heartbeat", "--self", who,
             "--project", self.tmp],
            capture_output=True, text=True)

    def test_heartbeat_refreshes_my_last_seen(self):
        self._participants([{"name": "Claude", "last_seen": OLD, "departed": False}])
        r = self._heartbeat("Claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        mine = next(a for a in self._read_parts() if a["name"] == "Claude")
        self.assertNotEqual(mine["last_seen"], OLD)

    def test_heartbeat_appends_me_if_absent(self):
        self._participants([{"name": "Codex", "last_seen": OLD}])
        self._heartbeat("Claude")
        self.assertIn("Claude", {a["name"] for a in self._read_parts()})

    def test_heartbeat_clears_my_departed_flag(self):
        self._participants([{"name": "Claude", "last_seen": OLD, "departed": True}])
        self._heartbeat("Claude")
        mine = next(a for a in self._read_parts() if a["name"] == "Claude")
        self.assertFalse(mine["departed"])

    def test_heartbeat_does_not_depart_a_stale_peer_or_bump_signal(self):
        self._participants([
            {"name": "Claude", "last_seen": OLD, "departed": False},
            {"name": "Codex", "last_seen": OLD, "departed": False}])
        sig_before = (self.collab / "collaboration_signal.json").read_text()
        self._heartbeat("Claude")
        codex = next(a for a in self._read_parts() if a["name"] == "Codex")
        self.assertFalse(codex.get("departed", False),
                         "heartbeat must NOT mark peers departed (that's board-wait's job)")
        self.assertEqual(sig_before,
                         (self.collab / "collaboration_signal.json").read_text(),
                         "heartbeat must NOT bump the signal")

    def test_heartbeat_preserves_another_agents_departed_flag(self):
        # REGRESSION (lost-update found in review): heartbeat takes the lock so it can
        # never resurrect a peer the departure scan just marked departed.
        self._participants([
            {"name": "Claude", "last_seen": OLD, "departed": False},
            {"name": "Codex", "last_seen": OLD, "departed": True}])
        self._heartbeat("Claude")
        codex = next(a for a in self._read_parts() if a["name"] == "Codex")
        self.assertTrue(codex["departed"],
                        "heartbeat must not clear ANOTHER agent's departed flag")


class KeepaliveScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration_participants.json").write_text(json.dumps(
            {"participants": [{"name": "Claude", "last_seen": OLD, "departed": False}]}))

    def _last_seen(self):
        parts = json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]
        return next(a for a in parts if a["name"] == "Claude")["last_seen"]

    def test_keepalive_refreshes_last_seen_and_writes_pidfile(self):
        p = subprocess.Popen(
            [str(KEEPALIVE), "--self", "Claude", "--project", self.tmp, "--interval", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            self.assertNotEqual(self._last_seen(), OLD, "keepalive should refresh last_seen")
            self.assertTrue((self.collab / ".keepalive_Claude.pid").exists())
        finally:
            p.terminate()
            p.wait()

    def test_keepalive_rejects_nonpositive_interval(self):
        r = subprocess.run(
            [str(KEEPALIVE), "--self", "Claude", "--project", self.tmp, "--interval", "0"],
            capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0, "--interval 0 must be rejected, not busy-spin")
        self.assertIn("positive", (r.stdout + r.stderr).lower())

    def test_keepalive_is_single_instance(self):
        p1 = subprocess.Popen(
            [str(KEEPALIVE), "--self", "Claude", "--project", self.tmp, "--interval", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            p2 = subprocess.run(
                [str(KEEPALIVE), "--self", "Claude", "--project", self.tmp, "--interval", "1"],
                capture_output=True, text=True, timeout=10)
            self.assertIn("already", (p2.stdout + p2.stderr).lower(),
                          "a second keepalive must refuse to start a duplicate")
        finally:
            p1.terminate()
            p1.wait()


if __name__ == "__main__":
    unittest.main()
