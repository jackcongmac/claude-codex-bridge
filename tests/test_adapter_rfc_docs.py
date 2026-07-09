import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RFC = ROOT / "docs" / "adapter-rfc.md"


class AdapterRfcDocsTests(unittest.TestCase):
    def test_adapter_rfc_covers_issue_10_acceptance_criteria(self):
        text = RFC.read_text()

        required = [
            "Status: RFC",
            "Do not implement adapters from this RFC",
            "Claude Code",
            "Codex",
            "Gemini CLI",
            "Aider",
            "OpenCode",
            "Cursor",
            "headless prompt",
            "session persistence",
            "machine-readable output",
            "permission controls",
            "cost reporting",
            "MCP compatibility",
            "install complexity",
            "safest first adapter candidate",
        ]
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_adapter_rfc_recommends_adapter_boundary_before_any_cli(self):
        text = RFC.read_text()

        self.assertIn("Adapter contract first", text)
        self.assertIn("No CLI-specific implementation should start", text)
        self.assertIn("Keep Claude Code + Codex as the primary pitch", text)

    def test_readme_links_to_adapter_rfc_without_expanding_the_pitch(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("docs/adapter-rfc.md", readme)
        # The v1.0 lead is "AI PR Gate for Claude Code + Codex"; the pitch must still
        # name BOTH agents up front (and stay two-agent, per the parked adapter RFC).
        self.assertIn("Claude Code", readme[:2000])
        self.assertIn("Codex", readme[:2000])


if __name__ == "__main__":
    unittest.main()
