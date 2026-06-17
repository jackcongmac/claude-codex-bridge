import os
import pathlib
import select
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class BoardWaitStayArmedTests(unittest.TestCase):
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
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        for pidfile in self.collab.glob(".boardwait_*.pid"):
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
            except Exception:
                pass

    def _join(self, who):
        subprocess.run([str(SCRIPTS / "join-collaboration.sh"),
                        "--self", who, "--role", "peer", "--project", self.tmp],
                       capture_output=True, text=True, check=True)

    def _wait_for_pidfile(self, who):
        pidfile = self.collab / (".boardwait_%s.pid" % who)
        for _ in range(50):
            if pidfile.exists():
                return pidfile
            time.sleep(0.1)
        self.fail("%s did not arm board-wait" % who)

    def _read_until(self, proc, needle, timeout=8):
        deadline = time.time() + timeout
        seen = ""
        fd = proc.stdout.fileno()
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                chunk = os.read(fd, 4096).decode()
                seen += chunk
                if needle in seen:
                    return seen
            if proc.poll() is not None:
                break
        self.fail("did not see %r before exit/timeout; saw: %s" % (needle, seen))

    def test_stay_armed_survives_peer_update_and_still_pongs_handshake(self):
        self._join("Claude")
        self._join("Codex")
        env = {**os.environ, "BRIDGE_BOARD_WAIT_INTERVAL": "0.2"}
        claude_waiter = subprocess.Popen(
            [str(SCRIPTS / "board-wait.sh"), "--self", "Claude",
             "--project", self.tmp, "--interval", "0.2", "--timeout", "60"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        self.procs.append(claude_waiter)
        self._wait_for_pidfile("Claude")
        waiter = subprocess.Popen(
            [str(SCRIPTS / "board-wait.sh"), "--self", "Codex",
             "--project", self.tmp, "--interval", "0.2",
             "--timeout", "60", "--stay-armed"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.procs.append(waiter)
        pidfile = self._wait_for_pidfile("Codex")

        subprocess.run([str(SCRIPTS / "bridge-post.sh"),
                        "--self", "Claude", "--project", self.tmp,
                        "--message", "please stay armed",
                        "--summary", "stay armed regression"],
                       capture_output=True, text=True, check=True)

        output = self._read_until(waiter, "Inbox: ACTION_REQUIRED")
        self.assertIn("CHANGED update_id=", output)
        self.assertIn("stay armed regression", output)
        self.assertIn("Inbox: ACTION_REQUIRED", output)
        self.assertIn("Claude Outbox", output)
        self.assertIsNone(waiter.poll(), "stay-armed board-wait must not exit after wake")
        self.assertEqual(pidfile.read_text().strip(), str(waiter.pid))

        handshake = subprocess.run(
            [str(SCRIPTS / "bridge-handshake.sh"), "--self", "Claude",
             "--peer", "Codex", "--project", self.tmp, "--no-transport",
             "--timeout", "5", "--interval", "0.2"],
            capture_output=True, text=True, env=env)
        self.assertEqual(handshake.returncode, 0, handshake.stdout + handshake.stderr)
        self.assertIn("握手成功", handshake.stdout)


if __name__ == "__main__":
    unittest.main()
