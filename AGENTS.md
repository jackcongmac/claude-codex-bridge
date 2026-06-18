# For any AI agent working in this repo (Codex reads this; Claude reads CLAUDE.md)

**This is a multi-agent collaboration project. Before doing project work, proactively
activate the collaboration channel — do not work in isolation.**

## Step 0 — Activation autostart (run this first, every fresh session)

```bash
scripts/board-wait.sh --self <YourName> --project . &
scripts/bridge-autostart.sh --self <YourName> --peer <PeerName> --role <peer|planner|executor|reviewer>
```

(`<YourName>` is your stable agent id this session, e.g. `Codex`, `codex-exec-1`,
`Claude`, `claude-rev`; `<PeerName>` is the other stable participant, usually
`Claude` or `Codex`.) `board-wait.sh` must be started by the agent/harness as the
tracked background task; it exits on peer updates, not quiet timeouts, and that
exit is what wakes the agent. `bridge-autostart.sh` then
performs the proactive handshake: it joins the board, starts liveness, runs
`bridge-handshake.sh`, and reports GO/NO-GO clearly. If NO-GO, it leaves a board
invite and prints the exact peer fix; that failure is non-blocking for work that
does not require a handoff.

## Reaching the peer — DRIVE, don't post-and-hope (DEFAULT operating mode)

The board is durable memory; it does **not** reliably WAKE the peer. A board post may
never wake the peer's interactive pane (Codex's pane is pull-only — it advances on user
input or its own polling, not on a background board event), which strands the handoff and
turns the **human into a relay**. The user does not care how the two agents talk; they
care that work ships without them shuttling messages. So this is permanent and non-optional:

When you need the peer to DO something (review a SHA, push, implement a spec), use the
**HYBRID**:

1. **Drive it directly** through the peer's tool channel — the spawned worker runs
   reliably (unlike the interactive pane):
   - **Codex → Claude:** `ask_claude` (the `claude_chat` MCP) with a TIGHT bounded prompt.
   - **Claude → Codex:** `mcp__codex__codex` with `sandbox: workspace-write`,
     `approval-policy: never`, `cwd: <repo>`.
2. **Record it on the board** — post the task and the result to the Outbox (and use the
   inbox `ACK`/`CLAIM`/`DONE`), so the durable log stays complete and auditable.

**Board = the record. Direct call = the actuator. The human is NEVER the message bus.**
Narrate before each direct call (it is opaque/slow). The direct call spawns a fresh worker
(less context, a real run cost), so it is for bounded handoffs, not open-ended work.

## The non-negotiables (full detail in docs/agent-collaboration-protocol.md)

1. **Stay synced.** ARM `board-wait.sh --self <You>` in the background after every
   turn. "I posted but the peer didn't react" = a missing ARM, not a dead channel.
2. **Treat peer Outbox as your Inbox.** When `board-wait` wakes on `<Peer> Outbox`,
   immediately run `scripts/bridge-inbox.sh pending --self <You> --project .`.
   Before or while acting, record `ACK`, `CLAIM`, `DECLINE`, or `DONE` with
   `scripts/bridge-inbox.sh ack --self <You> --status CLAIM --note "..."`. LIVE
   without an inbox receipt is not a handled handoff.
3. **Push through `scripts/bridge-push.sh <you>`** — never a bare `git push`
   (two agents pushing at once collide).
4. **Respect file lanes** (see the protocol doc). Cross-lane edits get announced
   on the board first.
5. **Review before merge.** Author self-review + tests → the *other* AI reviews
   (GO/REVISE · SHIP/FIX-FIRST) → human approves direction.
6. **Coordinate releases on the board** — check existing tags
   (`gh release list`) before cutting one, so versions don't drift.
7. **Narrate before slow/opaque actions** — both agents. Before a peer call
   (`mcp__codex__codex` / `ask_claude`) or any long step the user can't watch,
   say in one line WHAT you're about to do and ROUGHLY how long, and what a normal
   wait looks like. A predictable heads-up before each opaque step is how the two
   agents earn the user's trust; an un-narrated slow action reads as "stuck."
8. **Proactive handshake on activation; handshake before handoff.** Start every fresh
   session by arming `board-wait.sh` as the harness-tracked background task, then run
   `scripts/bridge-autostart.sh`. Don't dump a task into the board or fire a blocking
   peer call until the channel is confirmed live. If you need to re-check, run
   `scripts/bridge-handshake.sh --self <You> --peer <Them>` first: it fast-fails with
   a fix if the peer isn't ARMed/joined, and prints a GO confirmation when both sides
   are listening. Silence from the peer = a failed handshake, not a dead channel —
   never leave the user staring at a hung call.

If `bridge-autostart.sh` itself is unavailable, fall back to
`scripts/join-collaboration.sh --self <YourName> --role <role>`, then ARM
`board-wait.sh` manually through the harness and run `bridge-handshake.sh`.
