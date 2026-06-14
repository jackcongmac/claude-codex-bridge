import json
import os
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))
import _surface  # noqa: E402
import claude_chat_mcp  # noqa: E402


class DetectSurfaceTests(unittest.TestCase):
    """_surface.detect(env) — confidently recognizes Claude Code (confirmed env),
    honors BRIDGE_SURFACE override, returns 'unknown' otherwise (no guessing)."""

    def test_claude_code_cli_can_run_scripts(self):
        d = _surface.detect({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})
        self.assertEqual(d["agent"], "claude-code")
        self.assertEqual(d["surface"], "cli")
        self.assertTrue(d["shell"])

    def test_claude_code_noncli_entrypoint_is_not_shell_capable(self):
        d = _surface.detect({"CLAUDE_CODE_ENTRYPOINT": "sdk"})
        self.assertEqual(d["agent"], "claude-code")
        self.assertEqual(d["surface"], "non-cli")
        self.assertFalse(d["shell"])

    def test_agent_override_for_a_known_cli_agent(self):
        d = _surface.detect({"BRIDGE_AGENT": "codex", "CLAUDECODE": "1"})
        self.assertEqual(d["agent"], "codex")
        self.assertEqual(d["surface"], "cli")
        self.assertTrue(d["shell"])
        self.assertEqual(d["source"], "override")

    def test_forced_noncli_surface_is_never_shell_capable(self):
        # the FIX: forcing a desktop/non-cli surface must NOT be marked shell-capable
        d = _surface.detect({"BRIDGE_AGENT": "claude-desktop", "BRIDGE_SURFACE": "non-cli"})
        self.assertEqual(d["agent"], "claude-desktop")
        self.assertEqual(d["surface"], "non-cli")
        self.assertFalse(d["shell"])

    def test_any_nonexplicit_cli_surface_value_defaults_to_noncli(self):
        # a typo'd / unknown BRIDGE_SURFACE value must fail SAFE (non-cli), not cli
        d = _surface.detect({"BRIDGE_SURFACE": "desktop"})
        self.assertEqual(d["surface"], "non-cli")
        self.assertFalse(d["shell"])

    def test_unknown_when_no_known_signal(self):
        d = _surface.detect({})
        self.assertEqual(d["agent"], "unknown")
        self.assertEqual(d["surface"], "unknown")
        self.assertIsNone(d["shell"])

    def test_report_cli_emits_json(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "_surface.py"), "report", "--json"],
            capture_output=True, text=True,
            env={**os.environ, "BRIDGE_AGENT": "codex"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["agent"], "codex")


class ClientLabelTests(unittest.TestCase):
    """claude_chat_mcp.client_label(params) — labels the calling MCP client from the
    initialize clientInfo, which is how a DESKTOP caller is told apart from a CLI
    caller (desktop reaches the bridge only over MCP, never via the shell)."""

    def test_clientinfo_present(self):
        self.assertEqual(
            claude_chat_mcp.client_label(
                {"clientInfo": {"name": "claude-desktop", "version": "1.2"}}),
            "claude-desktop/1.2")

    def test_clientinfo_absent(self):
        self.assertEqual(claude_chat_mcp.client_label({}), "unknown/?")

    def test_clientinfo_none(self):
        self.assertEqual(claude_chat_mcp.client_label(None), "unknown/?")

    def test_clientinfo_non_dict_does_not_throw(self):
        self.assertEqual(claude_chat_mcp.client_label({"clientInfo": "weird"}), "unknown/?")


if __name__ == "__main__":
    unittest.main()
