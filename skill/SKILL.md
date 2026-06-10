---
name: claude-codex-bridge
description: Use when the user wants Claude Code and Codex to talk to each other — setting up, debugging, or using the bidirectional MCP bridge so Codex can call Claude (mcp__claude_chat__ask_claude) and Claude can call Codex (mcp__codex__codex). Triggers on "let Codex talk to Claude", "Claude/Codex collaboration", "ask_claude", "claude_chat MCP", or installing this bridge.
---

# claude-codex-bridge

A bidirectional MCP bridge letting Claude Code and Codex call each other as tools.
The Claude side is a **persistent, project-aware colleague** (per-directory memory,
reads `collaboration.md`, can read/edit files — no shell).

Use the bridge to route work by agent strengths and subscription constraints, not
by round-robin turns. A common split is Claude Max for high-leverage reasoning /
review, and Codex Pro for bounded implementation / test iteration.

## Install / repair the bridge

Run the installer from the repo root (idempotent):

```bash
./install.sh
```

It detects `python3` / `claude` / `codex`, installs the wrapper to
`~/.claude-codex-bridge/`, adds `[mcp_servers.claude_chat]` to
`~/.codex/config.toml`, and registers `codex` as a user-scope Claude MCP server.
**Codex must be restarted** afterward to load the new server.

If `claude` is at a non-standard path: `CLAUDE_BIN=/path/to/claude ./install.sh`.

**Security:** by default the colleague can read AND edit/write files in the
caller's directory (no shell). For a read-only colleague install with
`BRIDGE_READONLY=1 ./install.sh`, or set
`CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"` on the `claude_chat` server.
See `docs/read-only-setup.md` for the safe evaluation setup and a redacted
config check.

## Using the two directions

- **Claude → Codex:** call `mcp__codex__codex` (returns a `threadId`); continue
  with `mcp__codex__codex-reply` passing that `threadId` to keep Codex's memory.
- **Codex → Claude:** call `mcp__claude_chat__ask_claude` with a `prompt`. Memory
  is auto-pinned per working directory — no need to pass `session_id`. Pass
  `new_session: true` to reset, or `session_id` to target a specific session.

## Recommended collaboration pattern (coordination layer)

The MCP servers are the transport; the coordination layer is what makes the two
agents collaborate. Drop the templates into a project:

```bash
scripts/init-collaboration.sh            # into current dir (idempotent)
```

This creates `collaboration.md` (shared board: roles, outboxes, file locks,
decision log) and `collaboration_signal.json` (low-token change signal).

The loop:
1. Each agent reads `collaboration_signal.json` first; re-reads `collaboration.md`
   only when `update_id` changed.
2. Each writes status/findings to its own outbox in `collaboration.md`, then bumps
   `collaboration_signal.json` (`update_id` + one-line `summary`).
3. Use the MCP bridge (`mcp__codex__codex` / `mcp__claude_chat__ask_claude`) to
   poke the other agent to take a turn.

The Claude colleague is already told to read `collaboration.md` automatically.

## Resource-aware routing

The default templates include a `max-claude-pro-codex` style resource strategy:

- **Claude Max:** architecture, ambiguity resolution, strict review, test
  strategy, large-context review, final QA.
- **Codex Pro:** implementation, search, small fixes, test iteration, mechanical
  docs updates.
- **Human:** scope, taste, risk, budget, and permission decisions.

Escalate to Claude when the next step needs broad context or judgment. Hand back
to Codex when the next step is a bounded implementation or verification task.
Ask the human when scope, risk, cost, permissions, or taste changes.

Watch the resource/safety state without editing files:

```bash
scripts/bridge-status.py --project .
scripts/bridge-status.py --project . --watch
```

Apply an opinionated role preset when the user wants the state file to encode a
specific split:

```bash
scripts/apply-role-preset.py --project . --preset max-claude-pro-codex
scripts/apply-role-preset.py --project . --preset reviewer-implementer
```

Presets reuse `roles` and `resource_profiles`; they do not grant extra write
permissions. Apply them while the loop is paused; the command refuses
`status:"active"` or an existing `collaboration.lock` unless `--force` is
passed.

## Troubleshooting

- **Codex doesn't see `claude_chat`:** it wasn't restarted, or
  `[mcp_servers.claude_chat]` is missing from `~/.codex/config.toml`.
- **Calls hang forever:** an MCP stdio server must read stdin with
  `readline()`, never `for line in sys.stdin` (block-buffers on a pipe so
  `initialize` never arrives). The shipped wrapper already does this.
- **"claude CLI not found":** set `CLAUDE_BIN` in the
  `[mcp_servers.claude_chat.env]` block of `~/.codex/config.toml`.
- **Colleague can't edit / read files:** check `CLAUDE_CHAT_ALLOWED_TOOLS`.

See `README.md` for architecture, configuration, and security notes.
