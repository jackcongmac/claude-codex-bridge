import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import claude_chat_mcp as w  # noqa: E402


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")


class FindBoardTests(unittest.TestCase):
    """The wrapper must point a spawned Claude at the REAL board (.collab/), resolved
    by walking up like find_project_root — not assume ./collaboration.md in cwd."""

    def test_finds_collab_board_from_nested_cwd(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        _touch(tmp / ".collab" / "collaboration.md")
        nested = tmp / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(w._find_board(str(nested)),
                         str(tmp / ".collab" / "collaboration.md"))

    def test_finds_legacy_flat_board(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        _touch(tmp / "collaboration.md")
        self.assertEqual(w._find_board(str(tmp / "a")),
                         str(tmp / "collaboration.md"))

    def test_stops_at_git_boundary_without_crossing_to_parent_board(self):
        parent = pathlib.Path(tempfile.mkdtemp())
        _touch(parent / ".collab" / "collaboration.md")  # parent project's board
        proj = parent / "proj"
        (proj / ".git").mkdir(parents=True)              # nested project, no board
        cwd = proj / "src"
        cwd.mkdir()
        self.assertIsNone(w._find_board(str(cwd)),
                          "must not bind to a parent project's board across a .git boundary")

    def test_no_board_returns_none(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        self.assertIsNone(w._find_board(str(tmp)))


class GroundingTests(unittest.TestCase):
    def test_grounding_points_at_the_resolved_board(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        _touch(tmp / ".collab" / "collaboration.md")
        g = w.grounding(str(tmp))
        self.assertIn(str(tmp / ".collab" / "collaboration.md"), g)

    def test_grounding_says_none_when_no_board(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        g = w.grounding(str(tmp))
        self.assertIn("No shared collaboration board", g)


class BaseCmdSignatureTests(unittest.TestCase):
    def test_base_cmd_embeds_the_resolved_board_in_grounding(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        _touch(tmp / ".collab" / "collaboration.md")
        cmd = w._base_cmd("hello", str(tmp))
        i = cmd.index("--append-system-prompt")
        self.assertIn(str(tmp / ".collab" / "collaboration.md"), cmd[i + 1])

    def test_every_base_cmd_call_passes_cwd(self):
        # regression guard: the stale-session retry path crashed when it called
        # _base_cmd(prompt) without cwd. Every _base_cmd(...) must take two args.
        import re
        src = (ROOT / "claude_chat_mcp.py").read_text()
        for argstr in re.findall(r"_base_cmd\(([^)]*)\)", src):
            self.assertIn(",", argstr, "a _base_cmd(...) is missing cwd: %r" % argstr)


if __name__ == "__main__":
    unittest.main()
