---
name: claude-codex-bridge
description: Use when the user wants Claude Code and Codex to collaborate — ESPECIALLY to SET UP collaboration in the current project. Triggers on "set up agent collaboration here", "装 collab", "let Claude and Codex work together in this project", "start a collaboration board", "init collaboration here", "have two agents collaborate", "make this project a shared agent workspace", as well as using/debugging the bridge ("let Codex talk to Claude", "ask_claude", "claude_chat MCP").
---

# claude-codex-bridge

A bidirectional MCP bridge letting Claude Code and Codex call each other as tools,
plus a per-project collaboration layer (a shared `.collab/` board both agents
join, react to, and coordinate through).

## Proactive offer (turn a one-off call into a real collaboration)

When you (the agent) are about to bridge to the OTHER agent for the first time in
this project — e.g. you're about to call `mcp__codex__codex` / `mcp__codex__codex-reply`
(Claude side) or `mcp__claude_chat__ask_claude` (Codex side) — FIRST check whether
this project already has a shared board: does `<project-root>/.collab/` exist
(walk up from cwd; also treat a root `AGENTS.md`/`CLAUDE.md` as "already set up")?

- **If a board already exists:** just proceed (and ideally read it / join).
- **If NOT, ask the user ONE line before making the one-off call:**
  > "This project has no shared collaboration board yet. Want me to set one up so
  > Claude and Codex can collaborate persistently here (shared `.collab/` board both
  > join, react to, and coordinate through) — or just make this a one-off call?"
  - **User says set it up / yes:** run the setup below, then proceed.
  - **User says just this once / no:** make the single MCP call without a board.

Don't nag: offer at most once per project. Once `.collab/` exists, never re-ask.

## Set up collaboration in THIS project (the one-phrase trigger)

When the user asks to set up / start collaboration in the current project (or
accepts the proactive offer above), do this:

1. Resolve the bridge scripts dir — read the FIRST line of
   `~/.claude-codex-bridge/scripts_path` (written by install.sh; the single source
   of truth). If that file is missing, fall back to a known clone, e.g.
   `~/claude-codex-bridge/scripts` or wherever the repo lives.
2. **If you have a shell:** run `"$SCRIPTS/init-collaboration.sh" "$PWD"`. This
   creates `<project-root>/.collab/` (the shared board/signal/state/queue) and
   `AGENTS.md` / `CLAUDE.md` at the root (so every future agent in this project
   auto-discovers and joins). Then show the user the absolute `join-collaboration.sh`
   command it printed for the OTHER agent.
3. **If you have no shell:** print that exact command for the user to run.

Do NOT promise it "auto-happens" — you either run the command or hand the user the
single command. After init, the membership protocol takes over: any agent opening
this project reads the root `AGENTS.md`/`CLAUDE.md` and joins.

## Using the bridge once set up

The Claude side is a **persistent, project-aware colleague** (per-directory memory,
reads the board, can read/edit files — no shell).

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

This creates `.collab/collaboration.md` (shared board: roles, outboxes, file
locks, decision log) and `.collab/collaboration_signal.json` (low-token change signal).

The loop:
1. Each agent reads `.collab/collaboration_signal.json` first; re-reads the board
   only when `update_id` changed.
2. Each writes status/findings to its own outbox in `.collab/collaboration.md`, then
   bumps `.collab/collaboration_signal.json` (`update_id` + one-line `summary`).
3. Use the MCP bridge (`mcp__codex__codex` / `mcp__claude_chat__ask_claude`) to
   poke the other agent to take a turn.

The Claude colleague is already told to read the board automatically.

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
