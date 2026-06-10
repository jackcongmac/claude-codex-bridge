import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "windows-support.md"


class WindowsSupportDocsTests(unittest.TestCase):
    def test_windows_notes_cover_issue_7_constraints(self):
        text = DOC.read_text()

        required = [
            "not yet verified",
            "Claude",
            "Codex",
            "config.toml",
            "install.sh",
            "PowerShell",
            "watcher",
            "file locking",
        ]
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_windows_notes_do_not_claim_native_support(self):
        text = DOC.read_text()

        self.assertIn("Do not treat Windows as supported", text)
        self.assertIn("WSL", text)
        self.assertIn("Git Bash", text)
        self.assertIn("%USERPROFILE%\\.codex\\config.toml", text)

    def test_readme_links_to_windows_notes(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("docs/windows-support.md", readme)


if __name__ == "__main__":
    unittest.main()
