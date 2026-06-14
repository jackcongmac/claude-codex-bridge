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
LV = SCRIPTS / "_liveness.py"
BRIDGE_LIVENESS = SCRIPTS / "bridge-liveness.sh"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") + " PDT"


class LivenessVerdictTests(unittest.TestCase):
    """_liveness.py bases the verdict on PRESENCE (last_seen), not the flappy
    board-wait pidfile — so a present agent in its re-arm gap reads PRESENT, never
    DEAD. See DESIGN_liveness.md."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 0, "updated_by": "none"}))

    def _write_participants(self, parts):
        (self.collab / "collaboration_participants.json").write_text(
            json.dumps({"participants": parts}))

    def _arm(self, name, pid):
        (self.collab / (".boardwait_%s.pid" % name)).write_text(str(pid))

    def _report(self, project=None):
        r = subprocess.run(
            [sys.executable, str(LV), "report", "--project", project or self.tmp, "--json",
             "--present-window", "60", "--stale-after", "1800"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {row["name"]: row for row in json.loads(r.stdout)}

    def test_present_and_armed_is_LIVE(self):
        now = time.time()
        self._write_participants([{"name": "Claude", "last_seen": _stamp(now - 2)}])
        self._arm("Claude", os.getpid())  # this test process is alive
        self.assertEqual(self._report()["Claude"]["verdict"], "LIVE")

    def test_present_but_not_armed_is_PRESENT_not_DEAD(self):
        # the normal re-arm gap: fresh heartbeat, no live pidfile
        now = time.time()
        self._write_participants([{"name": "Claude", "last_seen": _stamp(now - 3)}])
        self.assertEqual(self._report()["Claude"]["verdict"], "PRESENT")

    def test_dead_pid_in_pidfile_is_not_armed(self):
        now = time.time()
        self._write_participants([{"name": "Claude", "last_seen": _stamp(now - 3)}])
        self._arm("Claude", 999999)  # almost certainly not a live pid
        row = self._report()["Claude"]
        self.assertFalse(row["armed"])
        self.assertEqual(row["verdict"], "PRESENT")

    def test_between_present_window_and_stale_is_STALE(self):
        now = time.time()
        self._write_participants([{"name": "Codex", "last_seen": _stamp(now - 300)}])
        self.assertEqual(self._report()["Codex"]["verdict"], "STALE")

    def test_older_than_stale_is_DEAD(self):
        now = time.time()
        self._write_participants([{"name": "Codex", "last_seen": _stamp(now - 4000)}])
        self.assertEqual(self._report()["Codex"]["verdict"], "DEAD")

    def test_departed_flag_is_DEPARTED(self):
        now = time.time()
        self._write_participants([
            {"name": "Codex", "last_seen": _stamp(now - 2), "departed": True}])
        self.assertEqual(self._report()["Codex"]["verdict"], "DEPARTED")

    def test_unparseable_last_seen_is_STALE_not_optimistic(self):
        self._write_participants([{"name": "Codex", "last_seen": "not-a-time"}])
        self.assertEqual(self._report()["Codex"]["verdict"], "STALE")

    def test_reports_all_participants(self):
        now = time.time()
        self._write_participants([
            {"name": "Claude", "last_seen": _stamp(now - 2)},
            {"name": "Codex", "last_seen": _stamp(now - 2)}])
        rows = self._report()
        self.assertEqual(set(rows), {"Claude", "Codex"})

    def test_project_argument_can_be_inside_initialized_project(self):
        nested = pathlib.Path(self.tmp) / "docs" / "nested"
        nested.mkdir(parents=True)
        now = time.time()
        self._write_participants([{"name": "Claude", "last_seen": _stamp(now - 2)}])

        rows = self._report(str(nested))

        self.assertIn("Claude", rows)

    def test_agent_name_sanitization_matches_board_wait_ascii_rule(self):
        now = time.time()
        self._write_participants([{"name": "Codex-测试", "last_seen": _stamp(now - 2)}])
        # board-wait.sh uses: tr -c 'A-Za-z0-9_.-' '_' on UTF-8 bytes.
        self._arm("Codex-______", os.getpid())

        row = self._report()["Codex-测试"]

        self.assertTrue(row["armed"])
        self.assertEqual(row["verdict"], "LIVE")

    def test_public_bridge_liveness_wrapper_reports_from_nested_project(self):
        nested = pathlib.Path(self.tmp) / "docs" / "nested"
        nested.mkdir(parents=True)
        now = time.time()
        self._write_participants([{"name": "Claude", "last_seen": _stamp(now - 2)}])

        r = subprocess.run(
            [str(BRIDGE_LIVENESS), "report", "--project", str(nested), "--json",
             "--present-window", "60", "--stale-after", "1800"],
            capture_output=True, text=True)

        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)}
        self.assertEqual(rows["Claude"]["verdict"], "PRESENT")

    def test_readme_documents_liveness_report(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("scripts/bridge-liveness.sh", readme)
        self.assertIn("PRESENT", readme)
        self.assertIn("LIVE", readme)


if __name__ == "__main__":
    unittest.main()
