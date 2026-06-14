#!/usr/bin/env python3
"""_surface.py — detect which SURFACE / agent is invoking the bridge.

WHY: the coordination scripts (board-wait, presence-keepalive, handshake) only work
from a shell-capable CLI agent. A desktop GUI agent can't run them at all — so
handing a desktop user CLI commands is the cross-surface version of the silent hang.
This detector lets the skill give surface-correct instructions instead of blindly
assuming CLI.

Honest by design: it confidently recognizes Claude Code (confirmed runtime env:
CLAUDECODE / CLAUDE_CODE_ENTRYPOINT), and returns "unknown" otherwise rather than
guessing from unverified signals. A DESKTOP caller is detected at the MCP layer
instead — via clientInfo in the initialize handshake (see claude_chat_mcp.py) —
because a desktop app reaches the bridge only as an MCP client, never via the shell.

Overrides (for forcing/testing a surface the env can't confirm):
  BRIDGE_AGENT=<name>      name the agent (e.g. codex, claude-desktop)
  BRIDGE_SURFACE=cli|...   ONLY "cli" grants shell=True; any other value (e.g.
                           "non-cli") means shell=False — so forcing a desktop
                           caller can never be mislabeled as shell-capable.

CLI:  _surface.py report [--json]
"""
import argparse
import json
import os
import sys


def detect(env):
    """Return {agent, entrypoint, surface, shell, source} from an env mapping.

    surface : 'cli' (can run the bridge scripts) | 'non-cli' | 'unknown'
    shell   : True / False / None  (None = unknown)
    source  : where the verdict came from (override | env:claude-code | none)
    """
    forced_agent = env.get("BRIDGE_AGENT")
    forced_surface = env.get("BRIDGE_SURFACE")
    if forced_agent or forced_surface:
        # Only the literal "cli" grants shell-capability; any other BRIDGE_SURFACE
        # value (or declaring just an agent for a non-cli surface) stays non-cli, so
        # a forced desktop/non-cli caller can NEVER be mislabeled as shell-capable.
        surface = "cli" if (forced_surface or "cli") == "cli" else "non-cli"
        return {"agent": forced_agent or "override",
                "entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT", ""),
                "surface": surface, "shell": surface == "cli", "source": "override"}
    if env.get("CLAUDECODE") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT"):
        ep = env.get("CLAUDE_CODE_ENTRYPOINT") or "cli"
        is_cli = (ep == "cli")
        return {"agent": "claude-code", "entrypoint": ep,
                "surface": "cli" if is_cli else "non-cli",
                "shell": is_cli, "source": "env:claude-code"}
    return {"agent": "unknown", "entrypoint": "", "surface": "unknown",
            "shell": None, "source": "none"}


def report(args):
    info = detect(os.environ)
    if args.json:
        print(json.dumps(info))
    else:
        print("agent=%(agent)s surface=%(surface)s shell=%(shell)s "
              "entrypoint=%(entrypoint)s (via %(source)s)" % info)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return {"report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
