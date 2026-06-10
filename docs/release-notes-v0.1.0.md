# v0.1.0

Initial public release of `claude-codex-bridge`.

## Highlights

- Bidirectional Claude Code ↔ Codex MCP workflow.
- Codex can call Claude through `mcp__claude_chat__ask_claude`.
- Claude Code can call Codex through Codex's built-in MCP server.
- Persistent, project-aware Claude sessions pinned per working directory.
- Optional `collaboration.md` and `collaboration_signal.json` coordination
  layer for multi-agent handoffs.
- Autonomous watcher scripts for event-driven collaboration loops.
- Dependency-free Python stdlib MCP wrapper.
- Read-only install mode and explicit tool allowlist controls.

## Why it exists

Claude Code and Codex are both strong coding agents, but they normally operate
as separate tools. This bridge gives them a local transport and a lightweight
coordination convention so they can review, execute, and re-review each other's
work inside the same project.

The repository itself was built using the Claude + Codex collaboration pattern
it enables.

## Install

```bash
git clone https://github.com/jackcongmac/claude-codex-bridge.git
cd claude-codex-bridge
./install.sh
```

For read-only Claude access:

```bash
BRIDGE_READONLY=1 ./install.sh
```

Restart Codex after installing so it loads the `claude_chat` MCP server.

## Safety

By default, the Claude colleague can read and edit files in the working
directory where Codex calls it. It does not get shell access by default. Use
read-only mode or `CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"` if you only want
Claude to inspect files.

See `SECURITY.md` for details.
