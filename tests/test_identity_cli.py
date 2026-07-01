import os, shutil, subprocess, tempfile, unittest

def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None

SCRIPT = os.path.join("scripts", "bridge-identity.sh")

class IdentityCliTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = os.path.join(self.tmp, "keys")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".collab"))
        self.env = dict(os.environ, BRIDGE_KEYS_DIR=self.keys)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(["bash", SCRIPT, *args], cwd=".", env=self.env,
                              capture_output=True, text=True)

    def test_init_creates_0600_key_and_registers_pubkey(self):
        r = self._run("init", "--self", "Jack", "--project", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        key = os.path.join(self.keys, "Jack.key")
        self.assertTrue(os.path.exists(key))
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")
        allowed = os.path.join(self.proj, ".collab", "keys", "allowed_signers")
        self.assertIn("Jack ssh-ed25519 ", open(allowed).read())

    def test_verify_setup_roundtrips(self):
        self._run("init", "--self", "Jack", "--project", self.proj)
        r = self._run("verify-setup", "--self", "Jack", "--project", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reinit_rotates_single_line(self):
        self._run("init", "--self", "Jack", "--project", self.proj)
        self._run("init", "--self", "Jack", "--project", self.proj)
        allowed = os.path.join(self.proj, ".collab", "keys", "allowed_signers")
        lines = [l for l in open(allowed).read().splitlines() if l.startswith("Jack ")]
        self.assertEqual(len(lines), 1)

if __name__ == "__main__":
    unittest.main()
