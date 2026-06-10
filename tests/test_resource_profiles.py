import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ResourceProfileTemplateTests(unittest.TestCase):
    def test_state_template_declares_resource_profiles(self):
        state = json.loads((ROOT / "templates" / "collaboration_state.json").read_text())

        profiles = state["resource_profiles"]

        self.assertEqual(profiles["Claude"]["tier"], "max")
        self.assertIn("architecture", profiles["Claude"]["best_for"])
        self.assertIn("bulk_editing", profiles["Claude"]["avoid"])
        self.assertEqual(profiles["Codex"]["tier"], "pro")
        self.assertIn("implementation", profiles["Codex"]["best_for"])
        self.assertIn("large_context_synthesis", profiles["Codex"]["avoid"])

    def test_resource_aware_preset_documents_escalation_rules(self):
        doc = (ROOT / "docs" / "resource-aware-routing.md").read_text()

        self.assertIn("max-claude-pro-codex", doc)
        self.assertIn("Claude Max", doc)
        self.assertIn("Codex Pro", doc)
        self.assertIn("Escalate to Claude", doc)
        self.assertIn("Hand back to Codex", doc)
        self.assertIn("Ask the human", doc)

    def test_resource_aware_routing_documents_pairing_matrix_and_billing_modes(self):
        doc = (ROOT / "docs" / "resource-aware-routing.md").read_text()

        self.assertIn("Pairing Matrix", doc)
        self.assertIn("Claude Pro + Codex Pro", doc)
        self.assertIn("Claude Pro/Free + Codex Pro", doc)
        self.assertIn("API + credit-card auto-reload", doc)
        self.assertIn("Codex side has no parseable cost", doc)
        self.assertIn("balanced-pro-pro", doc)
        self.assertIn("reversed-pro-codex", doc)

    def test_auto_turn_envelope_includes_resource_profiles(self):
        source = (ROOT / "scripts" / "_auto_turn.py").read_text()

        self.assertIn('"resource_profiles": resource_profiles', source)
        self.assertIn('"your_resource_profile": resource_profiles.get(self_actor, {})', source)


if __name__ == "__main__":
    unittest.main()
