import json, os, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import _chat_roles as cr

class ChatRolesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.tmp, ".collab"))

    def _write(self, obj):
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            json.dump(obj, f)

    def test_defaults_when_missing(self):
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Human", "lead": ""})

    def test_loads_configured_roles(self):
        self._write({"human": "Jack", "lead": "Claude"})
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Jack", "lead": "Claude"})
        self.assertTrue(cr.is_human("Jack", self.tmp))
        self.assertFalse(cr.is_human("Claude", self.tmp))
        self.assertEqual(cr.lead_name(self.tmp), "Claude")

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(os.path.join(self.tmp, ".collab", "roles.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(cr.load_roles(self.tmp), {"human": "Human", "lead": ""})

if __name__ == "__main__":
    unittest.main()
