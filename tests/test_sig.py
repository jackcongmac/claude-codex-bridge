import os, shutil, subprocess, tempfile, unittest, sys
sys.path.insert(0, "scripts")
import _sig

def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None

def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test",
                    "-f", path], check=True, capture_output=True)

def _pub_line(actor, keypath):
    pub = open(keypath + ".pub").read().split()
    # allowed_signers line: "<principal> <keytype> <base64>"
    return "%s %s %s\n" % (actor, pub[0], pub[1])

class SigTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = os.path.join(self.tmp, "keys"); os.mkdir(self.keys)
        os.environ["BRIDGE_KEYS_DIR"] = self.keys
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".collab", "keys"))
        _gen_key(os.path.join(self.keys, "Jack.key"))
        with open(os.path.join(self.proj, ".collab", "keys", "allowed_signers"), "w") as f:
            f.write(_pub_line("Jack", os.path.join(self.keys, "Jack.key")))

    def tearDown(self):
        os.environ.pop("BRIDGE_KEYS_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sign_then_verify_true(self):
        payload = _sig.canonical("review", "Jack", "abc|GO", "2026-06-30 10:00:00 PDT", "n1")
        sig = _sig.sign("Jack", payload, project=self.proj)
        self.assertTrue(sig)
        self.assertTrue(_sig.verify("Jack", payload, sig, project=self.proj))

    def test_tampered_payload_fails(self):
        p1 = _sig.canonical("review", "Jack", "abc|GO", "t", "n1")
        sig = _sig.sign("Jack", p1, project=self.proj)
        p2 = _sig.canonical("review", "Jack", "abc|FIX-FIRST", "t", "n1")
        self.assertFalse(_sig.verify("Jack", p2, sig, project=self.proj))

    def test_wrong_actor_principal_fails(self):
        payload = _sig.canonical("chat", "Jack", "h", "t", "n1")
        sig = _sig.sign("Jack", payload, project=self.proj)
        self.assertFalse(_sig.verify("Codex", payload, sig, project=self.proj))

    def test_missing_private_key_returns_none(self):
        self.assertIsNone(_sig.sign("NoSuchActor", "p", project=self.proj))

    def test_missing_sig_verifies_false(self):
        self.assertFalse(_sig.verify("Jack", "p", None, project=self.proj))
        self.assertFalse(_sig.verify("Jack", "p", "", project=self.proj))

    def test_chat_payload_binds_text(self):
        m1 = {"speaker": "Jack", "text": "deploy now", "sent_at": "t", "_id": "id1"}
        m2 = {"speaker": "Jack", "text": "deploy NOW", "sent_at": "t", "_id": "id1"}
        self.assertNotEqual(_sig.chat_payload(m1), _sig.chat_payload(m2))

class GitignoreGuardTests(unittest.TestCase):
    def test_private_key_dir_ignored(self):
        gi = open(".gitignore").read()
        self.assertIn(".claude-bridge/keys", gi)
        self.assertIn("*.key", gi)

if __name__ == "__main__":
    unittest.main()
