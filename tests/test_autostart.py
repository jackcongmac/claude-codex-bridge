import json
import os
import pathlib
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AUTOSTART = SCRIPTS / "bridge-autostart.sh"


class BridgeAutostartTests(unittest.TestCase):
    """bridge-autostart is the activation entry point after the agent has armed its
    own harness-tracked board-wait. It must not fork/own board-wait itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        subprocess.run([str(SCRIPTS / "init-collaboration.sh"), self.tmp],
                       capture_output=True, text=True, check=True)
        self.collab = pathlib.Path(self.tmp) / ".collab"
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        for pattern in (".boardwait_*.pid", ".keepalive_*.pid"):
            for pidfile in self.collab.glob(pattern):
                try:
                    os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
                except Exception:
                    pass

    def _env(self):
        return {**os.environ, "BRIDGE_BOARD_WAIT_INTERVAL": "1"}

    def _join(self, who):
        subprocess.run([str(SCRIPTS / "join-collaboration.sh"),
                        "--self", who, "--role", "peer", "--project", self.tmp],
                       capture_output=True, text=True, check=True)

    def _arm(self, who):
        proc = subprocess.Popen([str(SCRIPTS / "board-wait.sh"), "--self", who,
                                 "--project", self.tmp, "--timeout", "60",
                                 "--interval", "1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=self._env())
        self.procs.append(proc)
        pidfile = self.collab / (".boardwait_%s.pid" % who)
        for _ in range(20):
            if pidfile.exists():
                return
            time.sleep(0.1)
        self.fail("%s did not arm board-wait" % who)

    def _run(self, *extra):
        return subprocess.run([str(AUTOSTART), "--project", self.tmp,
                               "--handshake-timeout", "5",
                               "--handshake-interval", "0.2",
                               "--keepalive-interval", "1",
                               "--no-transport", *extra],
                              capture_output=True, text=True, timeout=20,
                              env=self._env())

    def _board(self):
        return (self.collab / "collaboration.md").read_text()

    def test_go_path_uses_existing_self_arm_and_does_not_post_invite(self):
        self._join("Claude")
        self._join("Codex")
        self._arm("Claude")
        self._arm("Codex")
        pid_before = (self.collab / ".boardwait_Claude.pid").read_text().strip()

        r = self._run("--self", "Claude", "--peer", "Codex")

        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("channel LIVE", r.stdout)
        self.assertNotIn("please join+ARM", self._board())
        pid_after = (self.collab / ".boardwait_Claude.pid").read_text().strip()
        self.assertEqual(pid_before, pid_after, "autostart must not replace self board-wait")

    def test_no_go_posts_one_invite_and_reports_non_blocking_fix(self):
        self._join("Claude")
        self._join("Codex")
        self._arm("Claude")
        pid_before = (self.collab / ".boardwait_Claude.pid").read_text().strip()

        r = self._run("--self", "Claude", "--peer", "Codex")

        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NON-BLOCKING", r.stdout)
        self.assertIn("board-wait.sh --self \"Codex\"", r.stdout)
        board = self._board()
        self.assertEqual(board.count("please join+ARM"), 1)
        self.assertIn("@Codex", board)
        pid_after = (self.collab / ".boardwait_Claude.pid").read_text().strip()
        self.assertEqual(pid_before, pid_after, "autostart must not replace self board-wait")

    def test_does_not_create_orphan_self_boardwait_when_not_prearmed(self):
        self._join("Codex")

        r = self._run("--self", "Claude", "--peer", "Codex")

        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Self fix", r.stdout)
        self.assertNotIn("please join+ARM", self._board())
        self.assertFalse((self.collab / ".boardwait_Claude.pid").exists())

    def test_requires_self_and_peer(self):
        missing_self = subprocess.run([str(AUTOSTART), "--peer", "Codex",
                                       "--project", self.tmp],
                                      capture_output=True, text=True)
        missing_peer = subprocess.run([str(AUTOSTART), "--self", "Claude",
                                       "--project", self.tmp],
                                      capture_output=True, text=True)

        self.assertNotEqual(missing_self.returncode, 0)
        self.assertIn("--self", missing_self.stderr)
        self.assertNotEqual(missing_peer.returncode, 0)
        self.assertIn("--peer", missing_peer.stderr)

    def test_activation_docs_point_to_autostart(self):
        for rel in ("AGENTS.md", "CLAUDE.md", "README.md", "skill/SKILL.md"):
            text = (ROOT / rel).read_text()
            self.assertIn("bridge-autostart.sh", text, rel)
            self.assertIn("activation", text.lower(), rel)
            self.assertIn("proactive handshake", text.lower(), rel)
            self.assertIn("harness", text.lower(), rel)


if __name__ == "__main__":
    unittest.main()
