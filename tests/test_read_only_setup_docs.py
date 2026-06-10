import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "read-only-setup.md"


class ReadOnlySetupDocsTests(unittest.TestCase):
    def test_read_only_guide_covers_safe_install_and_allowlist(self):
        text = DOC.read_text()

        self.assertIn("BRIDGE_READONLY=1 ./install.sh", text)
        self.assertIn('CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"', text)
        self.assertIn("Read Grep Glob", text)
        self.assertIn("Edit Write TodoWrite", text)

    def test_read_only_guide_includes_redacted_config_check(self):
        text = DOC.read_text()

        self.assertIn("~/.codex/config.toml", text)
        self.assertIn("redacted", text.lower())
        self.assertIn("CLAUDE_CHAT_ALLOWED_TOOLS", text)
        self.assertIn("CLAUDE_BIN", text)

    def test_readme_and_skill_link_to_read_only_guide(self):
        readme = (ROOT / "README.md").read_text()
        skill = (ROOT / "skill" / "SKILL.md").read_text()

        self.assertIn("docs/read-only-setup.md", readme)
        self.assertIn("docs/read-only-setup.md", skill)


if __name__ == "__main__":
    unittest.main()
