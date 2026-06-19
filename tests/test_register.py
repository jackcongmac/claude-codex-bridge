import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PRESENCE = SCRIPTS / "_presence.py"
JOIN = SCRIPTS / "join-collaboration.sh"
sys.path.insert(0, str(SCRIPTS))
import bridge_common as bc  # noqa: E402

OLD = "2020-01-01 00:00:00 PDT"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") + " PDT"


class RegisterTests(unittest.TestCase):
    """join registration must be LOCKED: a lock-free participants read-modify-write
    can lose a concurrent join or clobber a locked presence/departure write."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 0}))

    def _register(self, who, role="peer"):
        return subprocess.run(
            [sys.executable, str(PRESENCE), "register", "--self", who,
             "--role", role, "--project", self.tmp],
            capture_output=True, text=True, timeout=20)

    def _parts(self):
        return json.loads(
            (self.collab / "collaboration_participants.json").read_text())["participants"]

    def _write_parts(self, parts):
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": parts}))

    def _arm(self, name, pid=None):
        (self.collab / (".boardwait_%s.pid" % name)).write_text(
            "%s\n" % (pid if pid is not None else os.getpid()))

    def test_register_adds_a_new_participant(self):
        r = self._register("Claude", "peer")
        self.assertEqual(r.returncode, 0, r.stderr)
        mine = next(a for a in self._parts() if a["name"] == "Claude")
        self.assertEqual(mine["role"], "peer")
        self.assertFalse(mine["departed"])
        self.assertTrue(mine.get("joined_at"))

    def test_register_is_idempotent_and_updates_role(self):
        self._register("Claude", "peer")
        self._register("Claude", "reviewer")
        claudes = [a for a in self._parts() if a["name"] == "Claude"]
        self.assertEqual(len(claudes), 1)
        self.assertEqual(claudes[0]["role"], "reviewer")

    def test_register_preserves_other_participants(self):
        (self.collab / "collaboration_participants.json").write_text(json.dumps(
            {"participants": [{"name": "Codex", "role": "peer",
                               "last_seen": OLD, "departed": True}]}))
        self._register("Claude", "peer")
        codex = next(a for a in self._parts() if a["name"] == "Codex")
        self.assertTrue(codex["departed"], "register must not clobber another member")

    def test_register_under_held_lock_fails_cleanly(self):
        p = bc.collab_paths(self.tmp)
        self.assertTrue(bc.acquire_lock(p["lock"], "holder", ttl=30, wait=0))
        try:
            r = subprocess.run(
                [sys.executable, str(PRESENCE), "register", "--self", "Claude",
                 "--role", "peer", "--project", self.tmp, "--wait", "1"],
                capture_output=True, text=True, timeout=20)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("LOCKBUSY", r.stdout + r.stderr)
            self.assertFalse((self.collab / "collaboration_participants.json").exists()
                             and any(a["name"] == "Claude" for a in self._parts()))
        finally:
            bc.release_lock(p["lock"])

    def test_register_keeps_requested_name_when_no_live_holder_exists(self):
        r = self._register("Claude", "peer")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "Claude")
        self.assertIn("Claude", {a["name"] for a in self._parts()})

    def test_register_assigns_suffix_when_requested_name_is_reactive(self):
        self._write_parts([{"name": "Claude", "role": "peer",
                            "joined_at": _stamp(time.time() - 10),
                            "last_seen": _stamp(time.time() - 2),
                            "departed": False}])
        self._arm("Claude")

        r = self._register("Claude", "peer")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "Claude-2")
        self.assertEqual({"Claude", "Claude-2"}, {a["name"] for a in self._parts()})

    def test_register_reuses_requested_name_when_existing_holder_is_not_reactive(self):
        self._write_parts([{"name": "Claude", "role": "peer",
                            "joined_at": OLD, "last_seen": OLD,
                            "departed": False}])

        r = self._register("Claude", "reviewer")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "Claude")
        claudes = [a for a in self._parts() if a["name"] == "Claude"]
        self.assertEqual(len(claudes), 1)
        self.assertEqual(claudes[0]["role"], "reviewer")

    def test_join_prints_the_assigned_final_name(self):
        (self.collab / "collaboration.md").write_text("## Participants\n")
        self._write_parts([{"name": "Claude", "role": "peer",
                            "joined_at": _stamp(time.time() - 10),
                            "last_seen": _stamp(time.time() - 2),
                            "departed": False}])
        self._arm("Claude")

        r = subprocess.run(
            [str(JOIN), "--self", "Claude", "--role", "peer", "--project", self.tmp],
            capture_output=True, text=True, timeout=20)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("joined as: Claude-2", r.stdout)
        self.assertIn('board-wait.sh --self "Claude-2"', r.stdout)


if __name__ == "__main__":
    unittest.main()
