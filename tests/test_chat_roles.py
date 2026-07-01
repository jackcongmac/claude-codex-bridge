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

    def _participants(self, participants):
        with open(os.path.join(self.tmp, ".collab", "collaboration_participants.json"), "w") as f:
            json.dump({"participants": participants}, f)

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

    def test_chat_peers_defaults_to_exact_claude_codex_and_ignores_registry(self):
        self._participants([
            {"name": "Claude"},
            {"name": "Codex"},
            {"name": "Codex-2"},
            {"name": "Jack"},
        ])

        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

    def test_chat_peers_current_setup_resolves_exact_default_agents(self):
        self._write({"human": "Jack", "lead": "Claude"})
        self._participants([{"name": "Claude"}, {"name": "Codex"}, {"name": "Codex-2"}])

        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

    def test_chat_peers_uses_configured_agents_order(self):
        self._write({"human": "Jack", "agents": ["Alice", "Bob"]})

        self.assertEqual(cr.chat_peers(self.tmp), ("Alice", "Bob"))

    def test_chat_peers_ignores_registered_participant_not_in_config(self):
        self._write({"human": "Jack", "agents": ["Alice", "Bob"]})
        self._participants([
            {"name": "Dana"},
            {"name": "Alice"},
            {"name": "Bob"},
            {"name": "Jack"},
        ])

        self.assertEqual(cr.chat_peers(self.tmp), ("Alice", "Bob"))

    def test_chat_peers_never_includes_human_from_config(self):
        self._write({"human": "Jack", "agents": ["Jack", "Dana"]})
        self._participants([{"name": "Jack"}, {"name": "Codex-2"}])

        self.assertEqual(cr.chat_peers(self.tmp), ("Dana",))

    def test_chat_peers_tolerates_missing_or_malformed_roster_sources(self):
        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

        with open(os.path.join(self.tmp, ".collab", "collaboration_participants.json"), "w") as f:
            f.write("{not json")

        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

        self._write({"human": "Jack", "agents": [None, ""]})

        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

    def test_chat_peers_never_empty(self):
        self._write({"human": "Jack", "agents": ["Jack"]})

        self.assertEqual(cr.chat_peers(self.tmp), ("Claude", "Codex"))

if __name__ == "__main__":
    unittest.main()
