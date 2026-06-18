# For Claude working in this repo (mirror of AGENTS.md, which Codex reads)

**This is a multi-agent collaboration project. Before doing project work, proactively
activate the collaboration channel — do not work in isolation.**

## Step 0 — Activation autostart (run this first, every fresh session)

```bash
scripts/board-wait.sh --self Claude --project . &
scripts/bridge-autostart.sh --self Claude --peer Codex --role <peer|planner|executor|reviewer>
```

`board-wait.sh` must be started by Claude Code as the harness-tracked background task;
it exits on peer updates, not quiet timeouts, and that exit is what wakes Claude.
`bridge-autostart.sh` then performs the proactive
handshake: joins the board, starts liveness, runs `bridge-handshake.sh`, and reports
GO/NO-GO clearly. If NO-GO, it leaves a board invite and prints the exact peer fix;
that failure is non-blocking for work that does not require a handoff.

## Reaching the peer — DRIVE, don't post-and-hope (DEFAULT operating mode)

The board is durable memory; it does **not** reliably WAKE the peer. Claude is
event-driven (its `board-wait` exit re-invokes it), but **Codex's interactive pane is
pull-only** — a board post may never wake it, which strands the handoff and turns the
**human into a relay**. The user does not care how the two agents talk; they care that
work ships without them shuttling messages. So this is permanent and non-optional:

When you need the peer to DO something (review a SHA, push, implement a spec), use the
**HYBRID**:

1. **Drive it directly** through the peer's tool channel — the spawned worker runs
   reliably (unlike the interactive pane):
   - **Claude → Codex:** `mcp__codex__codex` with `sandbox: workspace-write`,
     `approval-policy: never`, `cwd: <repo>`, and a TIGHT bounded prompt (point it at the
     exact Outbox item / commands; tell it to do only that and not start new work).
   - **Codex → Claude:** `ask_claude`.
2. **Record it on the board** — post the task and the result to the Outbox (and use the
   inbox `ACK`/`CLAIM`/`DONE`), so the durable log stays complete and auditable.

**Board = the record. Direct call = the actuator. The human is NEVER the message bus.**
Narrate before each direct call (it is opaque/slow — see the narration non-negotiable).
Caveat: the direct call spawns a fresh worker (less context, a real run cost), so it is
for bounded handoffs (review / push / implement-a-spec), not open-ended exploration.

## The non-negotiables (full detail in docs/agent-collaboration-protocol.md)

1. **Stay synced.** ARM `board-wait.sh --self Claude` in the background after every
   turn. "I posted but the peer didn't react" = a missing ARM, not a dead channel.
2. **Treat Codex Outbox as your Inbox.** When `board-wait` wakes on `Codex Outbox`,
   immediately run `scripts/bridge-inbox.sh pending --self Claude --project .`.
   Before or while acting, record `ACK`, `CLAIM`, `DECLINE`, or `DONE` with
   `scripts/bridge-inbox.sh ack --self Claude --status CLAIM --note "..."`. LIVE
   without an inbox receipt is not a handled handoff.
3. **Push through `scripts/bridge-push.sh claude`** — never a bare `git push`.
4. **Respect file lanes** (protocol doc). Cross-lane edits get announced on the
   board first.
5. **Review before merge.** Author self-review + tests → the *other* AI reviews
   (GO/REVISE · SHIP/FIX-FIRST) → human approves direction.
6. **Coordinate releases on the board** — check `gh release list` before cutting a
   version so numbers don't drift (this exact drift already happened once).
7. **Narrate before slow/opaque actions** — both agents. Before a peer call
   (`mcp__codex__codex` / `ask_claude`) or any long step the user can't watch,
   say in one line WHAT you're about to do and ROUGHLY how long, and what a normal
   wait looks like. A predictable heads-up before each opaque step is how the two
   agents earn the user's trust; an un-narrated slow action reads as "stuck."
8. **Proactive handshake on activation; handshake before handoff.** Start every fresh
   session by arming `board-wait.sh` as the harness-tracked background task, then run
   `scripts/bridge-autostart.sh`. Don't dump a task into the board or fire a blocking
   peer call until the channel is confirmed live. If you need to re-check, run
   `scripts/bridge-handshake.sh --self Claude --peer <Them>` first: it fast-fails
   with a fix if the peer isn't ARMed/joined, and prints a GO confirmation when both
   sides are listening. Silence from the peer = a failed handshake, not a dead
   channel — never leave the user staring at a hung "Calling…".

If `bridge-autostart.sh` itself is unavailable, fall back to
`scripts/join-collaboration.sh --self Claude --role <role>`, then ARM `board-wait.sh`
manually through the harness and run `bridge-handshake.sh`.
