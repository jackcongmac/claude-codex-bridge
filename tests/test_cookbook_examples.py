import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COOKBOOK = ROOT / "examples" / "cookbook.md"


class CookbookExampleTests(unittest.TestCase):
    def test_cookbook_contains_issue_5_workflows(self):
        text = COOKBOOK.read_text()

        expected_sections = [
            "Review -> Implement -> Re-review",
            "Run Tests And Summarize Failures",
            "Docs Update After Implementation",
            "Shared Board Handoff",
            "Claude Max + Codex Pro Preset",
        ]
        for section in expected_sections:
            with self.subTest(section=section):
                self.assertIn(section, text)

    def test_each_cookbook_entry_has_prompt_and_handoff(self):
        text = COOKBOOK.read_text()

        self.assertGreaterEqual(text.count("Tool call prompt"), 5)
        self.assertGreaterEqual(text.count("Expected handoff"), 5)
        self.assertIn("mcp__claude_chat__ask_claude", text)
        self.assertIn("mcp__codex__codex", text)
        self.assertIn("collaboration_signal.json", text)

    def test_readme_links_to_cookbook(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("examples/cookbook.md", readme)


if __name__ == "__main__":
    unittest.main()
