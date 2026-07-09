import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _pr_evidence as ev  # noqa: E402


def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None


def _gen_key(path):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test", "-f", path],
        check=True, capture_output=True)


def _pub_line(actor, keypath):
    with open(keypath + ".pub") as f:
        pub = f.read().split()
    return "%s %s %s\n" % (actor, pub[0], pub[1])


class EvidenceEnvelopeTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = pathlib.Path(self.tmp) / "keys"
        self.keys.mkdir()
        os.environ["BRIDGE_KEYS_DIR"] = str(self.keys)
        self.proj = pathlib.Path(self.tmp) / "proj"
        (self.proj / ".collab" / "keys").mkdir(parents=True)
        _gen_key(str(self.keys / "Reviewer.key"))
        (self.proj / ".collab" / "keys" / "allowed_signers").write_text(
            _pub_line("Reviewer", str(self.keys / "Reviewer.key")))

    def tearDown(self):
        os.environ.pop("BRIDGE_KEYS_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _evidence(self):
        return ev.build_evidence(
            task="fix the gate",
            base_sha="b" * 40,
            head_sha="h" * 40,
            patch_ids=["p2", "p1", "p1"],
            test_cmd="python3 -m unittest",
            test_log="Ran 3 tests\nOK\n",
            tests_ok=True,
            branch="ai/fix-gate-bbbbbbb",
            implementer="Codex",
            reviewer="Reviewer",
            verdict="GO",
        )

    def test_build_hashes_task_and_test_log_and_sorts_patch_ids(self):
        e = self._evidence()

        self.assertEqual(e["task_hash"], ev.sha256_text("fix the gate"))
        self.assertEqual(e["test_log_hash"], ev.sha256_text("Ran 3 tests\nOK\n"))
        self.assertEqual(e["review_note_hash"], ev.sha256_text(""))
        self.assertIs(e["tests_ok"], True)
        self.assertEqual(e["patch_ids"], ["p1", "p2"])
        self.assertNotIn("fix the gate", json.dumps(e))
        self.assertNotIn("Ran 3 tests", json.dumps(e))
        self.assertTrue(e["nonce"])

    def test_build_hashes_reviewer_note(self):
        note = "WHY:\nThe change is scoped.\n\nDISAGREEMENTS/CONCERNS:\nnone\n\nVERDICT: GO clean"

        e = ev.build_evidence(
            task="fix the gate",
            base_sha="b" * 40,
            head_sha="h" * 40,
            patch_ids=["p1"],
            test_cmd="python3 -m unittest",
            test_log="Ran 3 tests\nOK\n",
            tests_ok=True,
            branch="ai/fix-gate-bbbbbbb",
            implementer="Codex",
            reviewer="Reviewer",
            verdict="GO",
            review_note=note,
        )

        self.assertEqual(e["review_note_hash"], ev.sha256_text(note))
        self.assertNotIn("The change is scoped", json.dumps(e))

    def test_canonical_is_deterministic_for_key_order(self):
        e = self._evidence()
        shuffled = {k: e[k] for k in reversed(list(e.keys()))}

        self.assertEqual(ev.canonical(e), ev.canonical(shuffled))
        self.assertEqual(ev.canonical(e), json.dumps(e, sort_keys=True, separators=(",", ":")))

    def test_sign_verify_roundtrip_and_head_matches(self):
        e = self._evidence()
        sig = ev.sign(e, "Reviewer", str(self.proj))

        self.assertTrue(sig)
        self.assertTrue(ev.verify(e, sig, str(self.proj)))
        self.assertTrue(ev.head_matches(e, "h" * 40))
        self.assertFalse(ev.head_matches(e, "x" * 40))

    def test_tampering_any_signed_field_fails_verify(self):
        e = self._evidence()
        sig = ev.sign(e, "Reviewer", str(self.proj))
        mutations = [
            ("head_sha", "x" * 40),
            ("verdict", "FIX-FIRST"),
            ("patch_ids", ["p1", "p3"]),
            ("test_log_hash", ev.sha256_text("different log")),
            ("review_note_hash", ev.sha256_text("different review")),
        ]

        for key, value in mutations:
            tampered = copy.deepcopy(e)
            tampered[key] = value
            with self.subTest(key=key):
                self.assertFalse(ev.verify(tampered, sig, str(self.proj)))

    def test_self_review_envelope_does_not_verify(self):
        e = self._evidence()
        e["implementer"] = "Reviewer"
        sig = ev.sign(e, "Reviewer", str(self.proj))

        self.assertFalse(ev.verify(e, sig, str(self.proj)))

    def test_empty_implementer_does_not_verify(self):
        e = self._evidence()
        e["implementer"] = ""
        sig = ev.sign(e, "Reviewer", str(self.proj))

        self.assertFalse(ev.verify(e, sig, str(self.proj)))

    def test_whitespace_implementer_does_not_verify(self):
        e = self._evidence()
        e["implementer"] = "  "
        sig = ev.sign(e, "Reviewer", str(self.proj))

        self.assertFalse(ev.verify(e, sig, str(self.proj)))

    def test_whitespace_case_variant_self_review_does_not_verify(self):
        e = self._evidence()
        e["implementer"] = " reviewer "
        sig = ev.sign(e, "Reviewer", str(self.proj))

        self.assertFalse(ev.verify(e, sig, str(self.proj)))

    def test_missing_reviewer_private_key_returns_none(self):
        e = self._evidence()

        self.assertIsNone(ev.sign(e, "MissingReviewer", str(self.proj)))


if __name__ == "__main__":
    unittest.main()
