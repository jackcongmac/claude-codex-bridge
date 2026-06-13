import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HS = SCRIPTS / "_handshake.py"


def _hs(*args, project):
    return subprocess.run(
        [sys.executable, str(HS), *args, "--project", str(project)],
        capture_output=True, text=True,
    )


class HandshakePingPongTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        collab = pathlib.Path(self.tmp) / ".collab"
        collab.mkdir()
        # a signal file makes collab_paths use the .collab layout
        (collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 0, "updated_by": "none"}))
        self.collab = collab

    def _handshake_json(self):
        return json.loads((self.collab / "collaboration_handshake.json").read_text())

    def test_ack_pongs_a_ping_addressed_to_it(self):
        r = _hs("ping", "--self", "Claude", "--peer", "Codex", "--ttl", "30",
                project=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        nonce = r.stdout.strip()
        self.assertTrue(nonce)

        _hs("ack", "--self", "Codex", project=self.tmp)

        poll = _hs("poll", "--self", "Claude", "--peer", "Codex", "--nonce", nonce,
                   "--timeout", "2", "--interval", "0.2", project=self.tmp)
        self.assertEqual(poll.returncode, 0, poll.stdout + poll.stderr)
        self.assertIn("PONG", poll.stdout)

    def test_ack_ignores_ping_addressed_to_a_different_peer(self):
        r = _hs("ping", "--self", "Claude", "--peer", "Codex", "--ttl", "30",
                project=self.tmp)
        nonce = r.stdout.strip()
        _hs("ack", "--self", "SomeoneElse", project=self.tmp)
        poll = _hs("poll", "--self", "Claude", "--peer", "Codex", "--nonce", nonce,
                   "--timeout", "1", "--interval", "0.2", project=self.tmp)
        self.assertNotEqual(poll.returncode, 0)
        self.assertIn("NOPONG", poll.stdout)

    def test_expired_ping_is_not_ponged(self):
        _hs("ping", "--self", "Claude", "--peer", "Codex", "--ttl", "0.001",
            project=self.tmp)
        import time
        time.sleep(0.1)
        _hs("ack", "--self", "Codex", project=self.tmp)
        self.assertEqual(self._handshake_json().get("pongs", {}), {})

    def test_two_simultaneous_handshakes_do_not_clobber(self):
        # Two initiators ping the same peer; one ack pass must satisfy BOTH nonces.
        n1 = _hs("ping", "--self", "Claude", "--peer", "Codex", "--ttl", "30",
                 project=self.tmp).stdout.strip()
        n2 = _hs("ping", "--self", "claude-rev", "--peer", "Codex", "--ttl", "30",
                 project=self.tmp).stdout.strip()
        self.assertNotEqual(n1, n2)
        _hs("ack", "--self", "Codex", project=self.tmp)
        for nonce, peer in ((n1, "Codex"), (n2, "Codex")):
            poll = _hs("poll", "--self", "X", "--peer", peer, "--nonce", nonce,
                       "--timeout", "1", "--interval", "0.2", project=self.tmp)
            self.assertEqual(poll.returncode, 0, "nonce %s was clobbered" % nonce)

    def test_ping_never_touches_the_signal(self):
        before = (self.collab / "collaboration_signal.json").read_text()
        _hs("ping", "--self", "Claude", "--peer", "Codex", project=self.tmp)
        _hs("ack", "--self", "Codex", project=self.tmp)
        after = (self.collab / "collaboration_signal.json").read_text()
        self.assertEqual(before, after, "handshake must not perturb the signal")


class BoardWaitHandshakeIntegrationTests(unittest.TestCase):
    """A live ARMed board-wait must pong; bridge-handshake must GO then NO-GO."""

    def test_live_armed_peer_gets_GO_then_NOGO_when_unarmed(self):
        tmp = tempfile.mkdtemp()
        env_int = {"BRIDGE_BOARD_WAIT_INTERVAL": "1"}
        import os
        env = {**os.environ, **env_int}
        subprocess.run([str(SCRIPTS / "init-collaboration.sh"), tmp],
                       capture_output=True, text=True)
        for who in ("Claude", "Codex"):
            subprocess.run([str(SCRIPTS / "join-collaboration.sh"),
                            "--self", who, "--role", "peer", "--project", tmp],
                           capture_output=True, text=True)
        waiters = []
        for who in ("Claude", "Codex"):
            waiters.append(subprocess.Popen(
                [str(SCRIPTS / "board-wait.sh"), "--self", who,
                 "--project", tmp, "--timeout", "60"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env))
        try:
            import time
            time.sleep(1.5)
            go = subprocess.run(
                [str(SCRIPTS / "bridge-handshake.sh"), "--self", "Claude",
                 "--peer", "Codex", "--project", tmp, "--no-transport",
                 "--timeout", "10", "--interval", "0.5"],
                capture_output=True, text=True, env=env)
            self.assertEqual(go.returncode, 0, go.stdout + go.stderr)
            # board-waits must still be alive — a handshake ping must not wake them
            self.assertIsNone(waiters[1].poll(), "Codex board-wait exited on a ping")

            waiters[1].terminate(); waiters[1].wait()
            nogo = subprocess.run(
                [str(SCRIPTS / "bridge-handshake.sh"), "--self", "Claude",
                 "--peer", "Codex", "--project", tmp, "--no-transport",
                 "--timeout", "4", "--interval", "0.5"],
                capture_output=True, text=True, env=env)
            self.assertNotEqual(nogo.returncode, 0)
            self.assertIn("握手失败", nogo.stdout)
        finally:
            for w in waiters:
                if w.poll() is None:
                    w.terminate()
                    w.wait()


if __name__ == "__main__":
    unittest.main()
