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
NOTIFY = SCRIPTS / "_notify.py"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") + " PDT"


class NotifyTransitionTests(unittest.TestCase):
    """_notify.py fires ONLY on a transition INTO DEAD/DEPARTED, debounced via a
    LOCKED .liveness_seen.json, bootstrapping quietly (first sight never pages)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(json.dumps({"update_id": 1}))
        (self.collab / "collaboration.md").write_text("# Board\n")

    def _participants(self, parts):
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": parts}))

    def _seen(self, d):
        (self.collab / ".liveness_seen.json").write_text(json.dumps(d))

    def _read_seen(self):
        return json.loads((self.collab / ".liveness_seen.json").read_text())

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def _tick(self):
        return subprocess.run(
            [sys.executable, str(NOTIFY), "tick", "--self", "Claude",
             "--project", self.tmp, "--stale-after", "1800"],
            capture_output=True, text=True, timeout=20)

    def test_bootstrap_is_quiet(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 5000)}])  # already DEAD
        r = self._tick()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("## Liveness", self._board(), "first sight must not page")
        self.assertEqual(self._read_seen().get("Codex"), "DEAD")  # recorded for next time

    def test_transition_into_dead_pages_once(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 5000)}])
        self._seen({"Codex": "PRESENT", "Claude": "PRESENT"})
        r = self._tick()
        self.assertIn("## Liveness", self._board())
        self.assertIn("Codex went DEAD", self._board())
        sig = json.loads((self.collab / "collaboration_signal.json").read_text())
        self.assertEqual(sig["changed_section"], "Liveness")
        # second tick: still dead, already seen -> no new note
        before = self._board().count("Codex went DEAD")
        self._tick()
        self.assertEqual(self._board().count("Codex went DEAD"), before)

    def test_departed_flag_pages(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 1), "departed": True}])
        self._seen({"Codex": "REACTIVE", "Claude": "PRESENT"})
        self._tick()
        self.assertIn("Codex went DEPARTED", self._board())

    def test_recovery_is_quiet(self):
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 1)}])  # now fresh
        self._seen({"Codex": "DEAD", "Claude": "PRESENT"})
        self._tick()
        self.assertNotIn("## Liveness", self._board(), "recovery must not page")
        self.assertNotEqual(self._read_seen().get("Codex"), "DEAD")  # state updated quietly

    def test_never_pages_about_self(self):
        now = time.time()
        self._participants([{"name": "Claude", "last_seen": _stamp(now - 5000)}])  # I'm "dead"
        self._seen({"Claude": "PRESENT"})
        self._tick()
        self.assertNotIn("## Liveness", self._board())

    def test_json_notify_keeps_stdout_valid_json_on_a_transition(self):
        # --json --notify must not corrupt stdout when a notification fires
        now = time.time()
        self._participants([
            {"name": "Claude", "last_seen": _stamp(now - 1)},
            {"name": "Codex", "last_seen": _stamp(now - 5000)}])
        self._seen({"Codex": "PRESENT", "Claude": "PRESENT"})
        r = subprocess.run(
            [str(SCRIPTS / "bridge-liveness.sh"), "report", "--self", "Claude",
             "--project", self.tmp, "--json", "--notify"],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)  # must parse despite the transition
        self.assertEqual({row["name"] for row in rows}, {"Claude", "Codex"})
        self.assertIn("Codex went DEAD", self._board())  # the note still fired


if __name__ == "__main__":
    unittest.main()
