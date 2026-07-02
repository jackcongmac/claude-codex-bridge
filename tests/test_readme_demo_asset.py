import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DEMO_GIF = ROOT / "docs" / "assets" / "claude-codex-bridge-demo.gif"


class ReadmeDemoAssetTests(unittest.TestCase):
    def test_readme_starts_with_x_line(self):
        readme = README.read_text(encoding="utf-8")

        self.assertTrue(readme.startswith("X\n"))

    def test_readme_embeds_terminal_demo_gif(self):
        readme = README.read_text(encoding="utf-8")

        self.assertIn("docs/assets/claude-codex-bridge-demo.gif", readme)
        self.assertRegex(readme, r"!\[[^\]]*Terminal demo[^\]]*\]")

    def test_demo_gif_is_valid_lightweight_animation(self):
        data = DEMO_GIF.read_bytes()

        self.assertTrue(data.startswith((b"GIF87a", b"GIF89a")))
        self.assertLess(len(data), 1_000_000)
        self.assertGreaterEqual(data.count(b"\x00\x2c"), 6)
