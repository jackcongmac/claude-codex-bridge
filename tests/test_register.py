import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PRESENCE = SCRIPTS / "_presence.py"
sys.path.insert(0, str(SCRIPTS))
import bridge_common as bc  # noqa: E402

OLD = "2020-01-01 00:00:00 PDT"


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


if __name__ == "__main__":
    unittest.main()
