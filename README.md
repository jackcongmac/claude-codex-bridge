# claude-codex-bridge

Bidirectional [MCP](https://modelcontextprotocol.io) bridge between
**Claude Code** and **Codex** — so each agent can call the other as a tool, with
the Claude side acting as a **persistent, project-aware colleague** rather than a
fresh stateless instance on every call.

```
              ┌──────────────────────────────────────────────┐
              │                                              │
   Claude Code ──► mcp__codex__codex ──► codex mcp-server ──► Codex
   (you)                                                       │
     ▲                                                         │
     │                                                         ▼
   ask_claude wrapper ◄── mcp__claude_chat__ask_claude ◄────── Codex
     │  (claude -p, persistent per-project session)
     ▼
   a Claude colleague that remembers + reads collaboration.md
```

| Direction | Tool the caller uses | What runs under the hood |
| --- | --- | --- |
| Claude → Codex | `mcp__codex__codex` / `mcp__codex__codex-reply` | `codex mcp-server` (Codex's built-in MCP mode) |
| Codex → Claude | `mcp__claude_chat__ask_claude` | `claude_chat_mcp.py` → `claude -p` |

## Why a wrapper for the Claude side?

Codex ships an MCP server mode (`codex mcp-server`) that exposes a single "run a
Codex session" tool — perfect for Claude→Codex. Claude Code's built-in
`claude mcp serve`, however, exposes Claude's **individual tools** (Read, Bash,
Edit, …), not a "chat with Claude" endpoint, and its `Agent` tool has **no
sub-agents registered** in headless serve mode (`Available agents:` is empty). So
to let Codex *talk to a reasoning Claude*, this project wraps `claude -p`
(headless print mode) in a tiny MCP server. That wrapper is the mirror image of
`codex mcp-server`.

## The persistent "colleague" behavior

`ask_claude(prompt, session_id?, new_session?)`:

- **Memory, auto-pinned per directory.** The first call in a working directory
  creates a Claude session with a fixed id and stores it under
  `~/.claude-codex-bridge/sessions/`. Every later call from that directory
  auto-`--resume`s it, so the same Claude accumulates context — Codex never has
  to manage a session id. Pass `session_id` to target a specific one, or
  `new_session: true` to reset.
- **Project grounding.** Each call appends a system prompt telling Claude it is
  the project's collaborator and to read `./collaboration.md` if it exists.
- **Powers: read + write, no shell.** Runs with
  `--allowedTools Read Grep Glob Edit Write TodoWrite` and
  `--permission-mode acceptEdits`. No `Bash`. No MCP servers are loaded inside
  the spawned Claude (`--strict-mcp-config --mcp-config '{"mcpServers":{}}'`),
  which keeps startup fast and prevents it from recursing back into Codex.

> **What it is *not*:** a literal always-on process that sees messages typed into
> your interactive Claude window in real time. CLI agent sessions can't be
> injected into from outside. Persistent session + shared `collaboration.md` +
> on-demand reach gives you ~90% of "online colleague" without that.

## Prerequisites

- [Claude Code](https://docs.claude.com/claude-code) CLI (`claude`) — logged in
- [Codex](https://developers.openai.com/codex) CLI (`codex`) — logged in
- `python3` (standard library only; no pip installs)

## Install

```bash
git clone https://github.com/<you>/claude-codex-bridge.git
cd claude-codex-bridge
./install.sh
```

The installer is idempotent. It:

1. Detects `python3`, `claude`, and `codex` (override the Claude path with
   `CLAUDE_BIN=/path/to/claude ./install.sh`).
2. Copies the wrapper to `~/.claude-codex-bridge/`.
3. Adds `[mcp_servers.claude_chat]` to `~/.codex/config.toml` (backing up first),
   pinning the detected `claude` path via `CLAUDE_BIN`.
4. Runs `claude mcp add codex -s user -- codex mcp-server` (user scope → all
   projects).

Then **restart Codex** so it loads the new server.

## Usage

In **Codex**:

```
call mcp__claude_chat__ask_claude with prompt:
"Read collaboration.md and give me your QA verdict on the current draft."
```

In **Claude Code**:

```
call mcp__codex__codex with prompt:
"Run the test suite and report failures."
# continue with mcp__codex__codex-reply using the returned threadId
```

Both directions are **global** after install — every project gets them, no
per-project setup.

## Configuration (env vars on the Codex `claude_chat` server)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_BIN` | auto-detected | path to the `claude` CLI |
| `CLAUDE_CHAT_SESSION_DIR` | `~/.claude-codex-bridge/sessions` | pinned-session store |
| `CLAUDE_CHAT_TIMEOUT` | `900` | per-call timeout (seconds) |
| `CLAUDE_CHAT_ALLOWED_TOOLS` | `Read Grep Glob Edit Write TodoWrite` | tool allowlist for the colleague |

To make the colleague read-only, set
`CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"`. To give it shell access, add
`Bash` (understand the risk: Codex could then drive arbitrary commands on your
machine through Claude).

## Gotcha that will bite you if you reimplement this

An MCP stdio server **must not** read its input with `for line in sys.stdin`.
On a pipe, Python block-buffers that iterator (it waits to fill ~8 KB before
yielding a line), so a lone `initialize` message never reaches your handler and
the MCP client hangs forever. It looks like it works when you feed several
messages at once in a test, then mysteriously hangs against a real client that
sends one message and waits. Use `while True: line = sys.stdin.readline()`
instead — it returns as soon as a newline arrives.

## Security notes

- The spawned Claude runs with **your** Claude Code credentials and can read (and
  by default edit) files in the working directory. Scope `CLAUDE_CHAT_ALLOWED_TOOLS`
  to your comfort level.
- Never commit your real `~/.codex/config.toml` — it may contain API keys for
  other MCP servers. This repo only ships a config *template* via the installer.

## License

MIT — see [LICENSE](LICENSE).
