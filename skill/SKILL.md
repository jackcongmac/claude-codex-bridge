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
this project already has a shared board. Walk UP from cwd using the SAME boundary
as `bridge_common.find_project_root` — stop at the nearest ancestor with `.collab/`,
or at the nearest `.git/` WITHOUT `.collab/` (never cross a nested git/submodule
boundary to a parent project's `.collab/`). It counts as "already set up" only if:
- `<root>/.collab/` exists, OR
- a root `AGENTS.md`/`CLAUDE.md` that **mentions** `claude-codex-bridge`, `.collab`,
  or `join-collaboration.sh` (a user's own unrelated `AGENTS.md` does NOT count).

- **If already set up:** just proceed (and ideally read the board / join).
- **If NOT, ask the user ONE line before making the one-off call:**
  > "This project has no shared collaboration board yet. Want me to set one up so
  > Claude and Codex can collaborate persistently here (shared `.collab/` board both
  > join, react to, and coordinate through) — or just make this a one-off call?"
  - **User says set it up / yes:** run the setup below, then proceed.
  - **User says just this once / no:** make the single MCP call without a board.

Don't nag: offer at most once per CONVERSATION, and never again once `.collab/`
exists. (Agent memory isn't durable across sessions, so this is a per-conversation
rule, not a permanent per-project one.)

## Two channels — and which one needs a handshake first

There are TWO ways to reach the other agent, and conflating them is the original
"就尬在那里" bug. Know which you're using:

- **One-off MCP call** (`mcp__codex__codex` / `mcp__codex__codex-reply` /
  `mcp__claude_chat__ask_claude`): spawns a **fresh, throwaway** peer instance that
  always answers — it is NOT the user's running window and is NOT on the board. Great
  for a single bounded question. No handshake needed (it can't hang on a missing peer;
  it spins one up). DO still narrate (it's slow — see narration rule below).
- **Persistent board hand-off** (post to `.collab/collaboration.md` + bump the
  signal, expecting the peer's *already-open, armed* window to react): this is real
  collaboration — and the ONLY one that silently hangs when the peer isn't joined +
  armed. **Before the first board hand-off of a session, run a handshake.** If you
  catch yourself about to write "I've told Codex, it'll pick this up" — stop, you're
  on the board channel; confirm it's live first (handshake GO, or a fresh peer
  reaction already seen this session).

Rule of thumb: *spawning* a peer never needs a handshake; *relying on a running
peer to react* always does. When a handshake is NO-GO and the user just wants a
quick answer, fall back to the one-off MCP call — but say so, so they know it's a
throwaway instance, not their collaborating window.

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

4. **Finish with a handshake, not a hope.** Once both agents have joined + ARMed,
   run `"$SCRIPTS/bridge-handshake.sh" --self <You> --peer <Them>` and show the
   user the result. GO prints a confirmation (peer live, board, round-trip) so the
   user can relax; NO-GO prints the exact fix (usually "ARM board-wait in the other
   window"). This is the antidote to "I told them to talk and it just hung": never
   hand real work to the peer — or tell the user it's collaborating — until a
   handshake (or a fresh peer reaction this session) confirms the channel is live.

## Open the group chat (trigger: "群聊" / "group chat")

When the user says **"群聊"**, **"group chat"**, **"打开群聊"**, **"open group chat"**
(or clearly asks for the shared chat room), LAUNCH the web group chat:

1. Resolve the bridge scripts dir (first line of `~/.claude-codex-bridge/scripts_path`),
   and the project root (it needs a `.collab/` board — offer to init one if absent).
2. Run it in the background:
   `"$SCRIPTS/bridge-chat-web.py" --self <the user's name, e.g. Jack> --project "<root>"`
   It serves a local window and opens the browser at `http://127.0.0.1:8765`.
3. Tell the user: **go to the browser to chat.** In the room: type `@` to pick who you
   mean (Claude / Codex / 所有人); **only the @-mentioned agent replies**, `@All` = both,
   no `@` = nobody auto-replies. Close with the **✕** top-right — that **archives the
   session** to `<root>/.collab/chat_archive/chat-<time>.md` and clears the live thread
   for next time.

It is one SESSION per window. Each close archives the full transcript (so past chats are
never lost) and starts the next session fresh.

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

### ALWAYS narrate before a slow/opaque action — BOTH agents, to build trust

This applies to **both** Claude and Codex (this skill is installed on both sides).
Before any action the user can't watch progress on — above all a peer call
(`mcp__codex__codex` / `mcp__codex__codex-reply` / `mcp__claude_chat__ask_claude`,
which spawn a whole other agent: tens of seconds to minutes, no streaming, one
silent "Calling…" line), but also long shells, big batch ops — tell the user in
one or two sentences FIRST:
1. **What** you're about to do and why;
2. **Roughly how long** (a magnitude, e.g. "~1–3 min — a full agent reading N
   files");
3. **What's normal vs stuck** — for a peer call: "you'll see `Calling…` with no
   output; that's expected, not frozen; only worry past ~X min."

Then act. Without this the user stares at a silent spinner, can't tell
working-from-hung, and cancels — treat an un-narrated slow action as a UX bug, not
a neutral one. A short, predictable heads-up before each opaque step is how the two
agents earn the user's trust over time. For heavy/long peer work, prefer the board
(`.collab/collaboration.md`) so progress is visible, over one big blocking call.

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
2. Each posts status/findings to its own outbox via
   `scripts/bridge-post.sh --self <You> --message "…"` — one locked step that appends
   to the board AND bumps the signal (don't hand-edit + bump separately; that can lose
   updates). Use `--section "<name>"` to post under another board section.
3. Use the MCP bridge (`mcp__codex__codex` / `mcp__claude_chat__ask_claude`) to
   poke the other agent to take a turn.

For full liveness in an interactive window, run:

```bash
scripts/bridge-live.sh --self <You> --project .
```

It registers you, starts the `presence-keepalive` singleton, reports liveness, and
prints the `board-wait` ARM command. It intentionally does not own `board-wait`,
because `board-wait` exits to wake the agent and must be re-armed after each turn.

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
