import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCTOR = SCRIPTS / "bridge-doctor.py"
THIS_HOST = socket.gethostname()
DEAD_PID = 999999


def _stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") + " PDT"


class DoctorTests(unittest.TestCase):
    """bridge-doctor diagnoses (and with --fix repairs) half-broken state: stale
    locks/pidfiles whose holder PID is dead ON THIS HOST, and departed-but-present
    participants. It never breaks a lock held on a DIFFERENT host."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": []}))

    def _lock(self, name, pid, host=THIS_HOST, age=9999):
        (self.collab / name).write_text(json.dumps(
            {"pid": pid, "host": host, "run_id": "x", "acquired_at": time.time() - age}))

    def _doctor(self, *args):
        return subprocess.run(
            [sys.executable, str(DOCTOR), "--project", self.tmp, *args],
            capture_output=True, text=True, timeout=20)

    def test_clean_project_has_no_issues(self):
        self.assertEqual(self._doctor().returncode, 0)

    def test_stale_lock_diagnosed_then_fixed(self):
        self._lock("collaboration.lock", DEAD_PID)
        self.assertNotEqual(self._doctor().returncode, 0)            # diagnosed
        self.assertTrue((self.collab / "collaboration.lock").exists())  # read-only
        self.assertEqual(self._doctor("--fix").returncode, 0)        # fixed
        self.assertFalse((self.collab / "collaboration.lock").exists())

    def test_live_lock_is_not_flagged(self):
        self._lock("collaboration.lock", os.getpid())               # alive
        self.assertEqual(self._doctor().returncode, 0)
        self.assertTrue((self.collab / "collaboration.lock").exists())

    def test_foreign_host_lock_is_reported_but_not_removed(self):
        self._lock("collaboration.lock", DEAD_PID, host="some-other-box")
        r = self._doctor("--fix")
        self.assertNotEqual(r.returncode, 0)                        # can't verify → unfixed
        self.assertTrue((self.collab / "collaboration.lock").exists())

    def test_hostless_lock_is_not_auto_fixed(self):
        self._lock("collaboration.lock", DEAD_PID, host="")   # no host recorded
        r = self._doctor("--fix")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.collab / "collaboration.lock").exists())

    def test_repair_respects_stale_after(self):
        # a participant 2000s stale is fresh under --stale-after 3600 → should be cleared
        (self.collab / "collaboration_participants.json").write_text(json.dumps(
            {"participants": [{"name": "Codex", "last_seen": _stamp(time.time() - 2000),
                               "departed": True}]}))
        self._doctor("--fix", "--stale-after", "3600")
        parts = json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]
        self.assertFalse(parts[0]["departed"])

    def test_departed_repair_never_breaks_a_foreign_lock(self):
        # a foreign collaboration.lock + a fresh departed participant: --fix must NOT
        # break the foreign lock to do the participants write.
        self._lock("collaboration.lock", DEAD_PID, host="some-other-box")
        (self.collab / "collaboration_participants.json").write_text(json.dumps(
            {"participants": [{"name": "Codex", "last_seen": _stamp(time.time() - 2),
                               "departed": True}]}))
        self._doctor("--fix")
        self.assertTrue((self.collab / "collaboration.lock").exists(),
                        "foreign lock must survive the departed repair")

    def test_stale_boardwait_pidfile_removed(self):
        (self.collab / ".boardwait_Codex.pid").write_text(str(DEAD_PID))
        self._doctor("--fix")
        self.assertFalse((self.collab / ".boardwait_Codex.pid").exists())

    def test_departed_but_fresh_participant_is_cleared(self):
        (self.collab / "collaboration_participants.json").write_text(json.dumps(
            {"participants": [{"name": "Codex", "last_seen": _stamp(time.time() - 2),
                               "departed": True}]}))
        self._doctor("--fix")
        parts = json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]
        self.assertFalse(parts[0]["departed"])


if __name__ == "__main__":
    unittest.main()
