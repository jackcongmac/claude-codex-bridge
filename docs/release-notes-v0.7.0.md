# v0.7.0 — Trust & onboarding: hand off with confidence, not hope

v0.6.0 made two agents reliably *find* each other. v0.7.0 makes the moment you
hand work between them **trustworthy and self-installing**: a fresh machine sets up
in one command, a fresh project in one phrase, and no hand-off happens into the
void — the channel is *confirmed live* first, with a visible result. Everything
below shipped since v0.6.0 and went through the project's own Claude+Codex review
loop (which caught a real concurrency bug before ship).

## Highlights

- **Pre-collaboration handshake + visible confirmation — kills the silent hang.**
  The recurring failure: told to "just contact each other," one side isn't actually
  ready (not restarted, not joined, `board-wait` not armed), so the board post /
  blocking peer call hangs in silence with no diagnosis ("就尬在那里"). New
  `bridge-handshake.sh` runs a fast preflight (board found · *I'm* armed · peer
  joined & fresh · transport wired) then a live ping, with a hard timeout — it can't
  hang. **GO** prints a confirmation (peer live, board, round-trip) so you can relax;
  **NO-GO** prints the exact fix. Now non-negotiable #7: *handshake before you hand
  off.*
  - The pong is written by the **peer's own `board-wait` at the harness layer**, so a
    reply *proves* the ARM mechanism real collaboration depends on is alive — it
    doesn't rely on the agent remembering to answer.
  - Runs on a **separate `collaboration_handshake.json`** channel that never touches
    the signal's `update_id`, so a ping can't mask or steal a real board update, and a
    handshake never wakes/interrupts the peer agent. Nonce-indexed, so concurrent
    handshakes can't clobber each other.

- **One-command install + one-phrase project setup.** `./install.sh` now sets up
  *everything*: transport (both MCP directions) **and** the skill, symlinked into
  **both** `~/.claude/skills/` and `~/.codex/skills/` so either agent can discover it,
  plus a recorded scripts path as the single source of truth. Then, in any project,
  one phrase — **"set up agent collaboration here"** — has the agent run
  `init-collaboration.sh` (or hand you the single command if it has no shell). No
  paths, no file names, no assembling pieces by hand.

- **Proactive offer to upgrade a one-off call into a real collaboration.** When an
  agent is about to bridge to the peer for the first time in a project with no board,
  it asks once whether to set up a persistent shared board — and on yes, runs the
  setup. Tight "already set up" detection (a user's own unrelated `AGENTS.md` doesn't
  count; respects nested git/submodule boundaries; per-conversation, not nagging).

- **Narrate before slow/opaque actions — bilateral, to build trust.** Both Claude and
  Codex now, before any peer call or long step the user can't watch, say in one line
  WHAT they're doing, ROUGHLY how long, and what normal-vs-stuck looks like. A
  predictable heads-up before each opaque step is how the two agents earn the user's
  trust over time; an un-narrated slow action reads as "stuck." Now non-negotiable #6.

## Fixes surfaced by real collaboration

- **board-wait single-armer mutex.** Duplicate armers for the same agent were the main
  source of false-departure noise; `board-wait` now claims an atomic (noclobber)
  per-(project,self) pidfile, breaks only a dead holder's stale claim, and cleans up
  only its own — sanitized agent ids included.
- **Handshake concurrency (caught in review).** The first cut used a single ping/pong
  slot; two simultaneous handshakes could clobber each other into a false NO-GO. Fixed
  to a nonce-indexed channel before ship; regression test added.
- `bridge-status.py` now shows the resolved project root + `.collab/` dir and drops
  dead constants.

## Upgrade notes

- **Re-run `./install.sh`** to get the skill installed on both sides and the recorded
  scripts path (idempotent; never overwrites a non-bridge skill symlink without
  warning). **Restart Codex** to load the transport.
- Before the first real hand-off of a session, run
  `scripts/bridge-handshake.sh --self <You> --peer <Them>` — GO means both armed and
  listening; NO-GO tells you exactly what to fix.
- Existing `.collab/` projects need nothing new; the handshake uses a new file in
  `.collab/` created on first ping.

## Honest boundaries (unchanged)

CLI agents, no in-window message injection, no true duplex streaming. The durable
shared board is the channel between already-running windows; MCP is the transport for
spawning/poking. The handshake confirms a *running, armed* peer — it can't make an
unopened window appear. Codex cost is governed by `max_turns` (not parseable as $).
