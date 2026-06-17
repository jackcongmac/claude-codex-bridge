# Feature inventory, shortcomings & roadmap

What claude-codex-bridge actually is, everything it does, where it falls short, and
what's next. The shortcomings + roadmap were found collaboratively: Claude drafted
them and an MCP-spawned Codex adversarially augmented and corrected them (the
project's own dual-review applied to its own audit). File references point at the
code as of this writing — verify before relying.

## What it is

Not quite a skill, agent, or harness — a **cross-harness coordination layer**: it
turns two independent agent CLIs (Claude Code + Codex), each a complete harness for
ONE agent, into peers that can find, reach, and stay in sync with each other.
Packaged as a skill (discovery), it adds capabilities neither CLI has natively: a
shared board, a wake mechanism, presence/liveness, a handshake, push/review
coordination. MCP is the transport; `.collab/` + the scripts are a mini coordination
harness on top.

## Features & highlights (🌟 = the genuinely novel bits)

**Transport — bidirectional MCP.** Claude→Codex (`mcp__codex__codex`/`-reply`),
Codex→Claude (a `claude_chat` wrapper using `readline`, not `for line in stdin`,
to avoid block-buffering deadlock). `install.sh` installs both directions + symlinks
the skill into `~/.claude/skills` and `~/.codex/skills`; read-only colleague option.

**Coordination board (`.collab/`).** `collaboration.md` (outboxes, file locks,
decision log, roles) + a low-token `collaboration_signal.json` (`update_id` +
`changed_section` + `summary`) so agents re-read the board only on change. 🌟
Project-rooted auto-location (`find_project_root` / `collab_paths` / `bridge-paths.sh`
— one source of truth for bash and python). `init-collaboration.sh` is idempotent and
never overwrites.

**Membership.** `join-collaboration.sh` registers an agent, prints the live rules +
board state + the ARM command; `collaboration_participants.json` tracks members.

**🌟🌟 Reactivity for interactive agents — `board-wait.sh` (the keystone).** A chat
agent is request/response and can't be woken by an external board write. board-wait
blocks in the background, exits on a peer change → the harness re-invokes the agent.
Single-armer noclobber pidfile mutex.

**Presence & liveness.** `_presence.py` heartbeat + departure broadcast;
🌟 `presence-keepalive.sh` keeps `last_seen` fresh independent of board-wait (so a
short window is honest); `bridge-liveness.sh` reports LIVE/PRESENT/STALE/DEAD/DEPARTED
at a glance (`--watch`), read-only; `bridge-live.sh` is the bounded go-live helper
that registers the agent, starts the keepalive singleton, reports liveness, and
prints the agent-owned `board-wait` ARM command.

**🌟 Handshake — `bridge-handshake.sh` + `_handshake.py`.** Preflight (board / I'm
armed / peer fresh / transport) + a live ping whose pong is written by the peer's
board-wait at the harness layer (proves the ARM mechanism is alive). Separate
nonce-indexed channel that never perturbs the signal; GO/NO-GO, never hangs.

**Surface detection (primitives) — `_surface.py` + `client_label` (MCP).** Recognizes
Claude Code from env, returns "unknown" rather than guessing; overrides fail safe; a
desktop caller is identifiable at the MCP layer via `clientInfo`. NOTE: these are
detection *primitives only* — not yet wired into the skill's instructions (clientInfo
is currently just logged to stderr). The intended use is surface-correct guidance
(don't hand a desktop user CLI commands), which is still to be integrated.

**Update tooling — `bridge-update.sh` + `_version.py`.** `--check` reports if the
clone is behind upstream (offline-safe: a failed fetch is "unknown", never a false
"up to date"); the default pulls `--ff-only`. The skill + scripts + (now) the
wrapper track the clone, so `git pull` updates everything (Codex restart still
reloads the wrapper). The wrapper is a symlink by default; `BRIDGE_WRAPPER_COPY=1`
or a filesystem without symlink support yields a copy that needs a re-install.

**Push coordination.** `bridge-push.sh` serializes `pull --rebase → push` under a
lock so two agents don't collide on the same branch.

**🌟 Review/governance protocol.** Seven non-negotiables: stay-synced/ARM, push only
via bridge-push, file lanes, review-before-merge (author → the OTHER AI → human),
release coordination, narrate-before-opaque, handshake-before-handoff.

**Autonomous mode.** `watch-collaboration.sh` + `_auto_turn.py`: 🌟 a deterministic
harness owns every protocol-critical operation (gates, lock, CAS, high-water,
budget, failure→halt); the model only produces content. State is paused + read-only
by default, with a `max_turns` cap (Codex cost isn't dollar-enforced — it's logged as
untracked, so `max_cost` does NOT bound Codex turns). (Autonomous mode DOES have
mechanized governance — role gates + reviewer-verdict validation + lock/CAS.)

**Multi-agent queue mode.** `_queue_turn.py`: N agents claim work-queue tasks under
lock with epoch fencing.

**Group chat delivery resync.** `_chat_respond.py`: responders scan unhandled chat
messages on startup and write per-agent handled ids to `.collab/chat_delivery.json`,
so an offline agent can replay missed @ messages with best-effort handled-id dedupe.
This is at-least-once delivery: a crash between posting a reply and recording handled
state can re-answer that one message. Current message ids are derived from timestamp,
speaker, and text, so same-second duplicate text from the same speaker can collide.

**Group chat thinking indicator.** `_chat_respond.py` writes `.collab/chat_typing.json`
while an agent is generating; `bridge-chat-web.py` exposes `/status` and renders a
compact "正在思考" line. Stale typing entries are hidden by age so a crashed responder
does not pin the UI forever.

**Group chat responder supervision.** While `bridge-chat-web.py` is serving a room, it
keeps one Claude responder and one Codex responder in order and restarts a responder
whose process exits. This is bounded to the web server lifetime; a full always-on
watcher/responder service remains the next reliability step.

**Resource routing + dashboards.** Role presets (`apply-role-preset.py`),
`bridge-status.py` (`--watch`). **Token control:** `compact-collaboration.sh` /
`_compact.py` archive-rotate the board losslessly under lock.

## Shortcomings (honest)

Grouped; 🔴 = highest priority. Several were surfaced by the adversarial pass and
several were lived during development.

**Trust & correctness** (roadmap #1 mechanized most of this — see below)
- ✅ **Transactional manual board writes (shipped, `bridge-post`).** A locked
  append-to-outbox + full-schema signal bump in one step (content-first ordering;
  exit 4 if the signal can't be bumped — never a lost message), and `join` now
  registers under the lock (`_presence.py register`). The locked `join` closes the
  unlocked-join gap, and the documented workflow (README / SKILL / join / protocol)
  now routes board posts through `bridge-post` instead of hand-edit+bump.
- ✅ **Review-before-merge is now gated (shipped, `bridge-push` + review ledger).**
  Push refuses (exit 4) any HEAD no peer recorded a SHIP/GO for; the only escape is an
  AUDITED `--no-review`. Converts the governance failure (push unreviewed + attribute
  a review that never happened) into a hard, auditable gate.
- 🔴 **Identity is still spoofable (open — the remaining trust gap).** `join` /
  `_handshake.py ack` / the review ledger trust `--self` with no secret or process
  binding. The gate makes a review an auditable artifact + hard gate, but a determined
  author could still self-certify by recording an entry as the peer. Anti-spoof needs
  identity binding.
- **File lanes are advisory** (announced, not locked); two agents can clobber the
  same file. The push-lock serializes pushes, not working-tree edits.
- **Release coordination is still convention** — the gate covers pushes, not GitHub
  releases/tags.

**Reachability & onboarding**
- **CLI-bound.** The coordination layer assumes a shell + harness re-invoke; no
  desktop/web participation. `_surface.py` now *detects* surface and `clientInfo` is
  captured at the MCP layer, but there is still no wake abstraction (a collab-MCP
  server) and clientInfo is only logged.
- **`join` doesn't itself start keepalive** — by design it remains a printable
  protocol join. `bridge-live.sh` now covers the practical go-live path by starting
  `presence-keepalive` and printing the `board-wait` ARM command.
- **`bridge-live` is intentionally not a board-wait supervisor** — `board-wait` exits
  as the wake signal, so a separate supervisor must not swallow that signal. The
  agent still has to run/re-arm the printed command after each turn.
- ✅ **MCP board discovery — fixed (roadmap #3).** `claude_chat_mcp.py` now resolves
  the real `.collab/collaboration.md` via `_find_board` (walks up like
  `find_project_root`), instead of assuming `./collaboration.md` in cwd.
- **MCP-spawn vs armed-window is an intrinsic footgun** (a one-off call spawns a
  throwaway, not the collaborating window).

**Liveness limits**
- liveness can't distinguish "window open but agent hung/idle" from "actively
  working" (keepalive proves the keepalive *process* is alive, not agent
  responsiveness).
- ✅ notify + revive shipped (roadmap #4): `bridge-liveness.sh --notify` pages once
  on a DEAD/DEPARTED transition; `bridge-revive.sh` re-ensures self + nudges a down
  peer. Plain report/`--watch` stay read-only.
- Reactivity depends on the agent reliably re-ARMing; no supervisor guarantees it
  (`bridge-live` reduces the footprint but board-wait re-arm is still the agent's job).

**Scale & environment**
- **Single-machine / shared-filesystem.** `.collab` is local files; cross-machine
  agents can't share a board. Worse: lock stale-breaking checks only local PID
  liveness and ignores the recorded `host`, so a live lock on another host can look
  dead locally. `last_seen` parsing assumes one local timezone/clock.
- **Codex autonomous turns lack session continuity** (Claude resumes a stored
  session; Codex does not — board context ≠ session memory).
- **Weak cost visibility** (Codex cost "not parseable as $"); budget caps exist but
  spend is opaque.

**Engineering**
- **Concurrency races aren't deterministically tested** (a real lost-update was
  caught by review, not a test). There are solid unit/integration tests (handshake,
  board-wait, keepalive, liveness, surface, version), but **no real MCP-transport
  E2E and no true two-window agent test**.
- ✅ **`bridge-doctor` shipped (roadmap #7)** — diagnoses/repairs stale locks
  (this-host only), dead pidfiles, and departed-but-present participants.
- **Published to npm, but no auto-update yet.** `@jackcongus/claude-codex-bridge@0.8.0`
  is live (`bridge-update` + version-check + symlinked wrapper underneath) — but there's
  no auto-update, no Claude Code plugin-marketplace listing yet, and `join` only *tells*
  the user to run the version check (doesn't run it).
- **Docs/mode sprawl** (manual / autonomous / queue, many `DESIGN_*.md`) raises
  onboarding cost.

## Strategic direction — be transport-agnostic; hold the governance layer

The space is active: several Claude↔Codex bridges already exist (e.g.
[codex-claude-bridge](https://github.com/abhishekgahlot2/codex-claude-bridge),
AgentBridge), bigger orchestrators (AWS
[cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator)), and —
crucially — Anthropic shipped [**Channels**](https://code.claude.com/docs/en/channels)
(Mar 2026, research preview): an MCP server that PUSHES events into a *running* Claude
Code session so the agent reacts without a human at the keyboard. Channels is, in
effect, an official wake primitive analogous to our board-wait+harness hack, and an
official Claude↔Codex live bridge is being tracked (claude-code #36871 / codex #15359).

Implication: **transport + wake are commoditizing / going official.** Our durable value
is the layer ABOVE them — the coordination + governance harness (shared board, locks,
the review-gate, presence/liveness, doctor, the dual-review protocol) that makes two
agents collaborate *safely*, not merely talk (the shared board, locks, the peer-review
gate, presence/liveness, doctor). Most transport bridges don't have it.

So the direction: **don't compete on transport/wake — be transport-AGNOSTIC.** The
coordination layer should ride on whatever wake exists underneath (board-wait today,
Channels when available, MCP-spawn for one-offs) and stay the constant on top. Caveats
to verify: Channels is Claude-Code (not confirmed for Claude Desktop), Anthropic-only
(Codex needs its own push), and only wakes an already-open session. One-liner:
*others are solving "let two agents talk"; we solve "let them work together safely."*

## Roadmap (prioritized)

1. ✅ **Mechanize trust — DONE (except identity binding).** Shipped: `bridge-post`
   (locked transactional board write), the review ledger + gated `bridge-push`
   (default hard-reject, audited `--no-review`), and locked `join` registration. The
   push gate is mechanically ENFORCED; and the documented workflow (README / SKILL /
   join / protocol) now routes board posts through `bridge-post` (no more hand-edit +
   bump). **Still open:** identity binding —
   until then `--self` is nominal, so the gate is auditable + hard but not anti-spoof
   on its own (a determined author could self-certify as the peer). Identity binding is
   the next trust slice.
2. ✅ **`bridge-live` go-live helper — bounded slice shipped.** One command
   registers the agent, starts/reuses `presence-keepalive`, reports liveness, and
   prints the exact `board-wait` ARM command. It does **not** supervise board-wait,
   because board-wait's exit is the harness wake signal and must remain visible to
   the agent.
3. ✅ **Fix MCP board discovery — DONE.** `claude_chat_mcp.py` now resolves the board
   via `_find_board(cwd)` (walks up like `find_project_root`, `.collab` then legacy
   flat, stops at a `.git` boundary) and names it in the per-call `grounding(cwd)`.
4. ✅ **Liveness notify + revive — DONE.** `bridge-liveness.sh --notify` (via
   `_notify.py`) pages once on a peer's transition into DEAD/DEPARTED (OS + a "##
   Liveness" board note, debounced, quiet bootstrap/recovery); `bridge-revive.sh`
   re-ensures self and nudges a down peer + notifies the human (no MCP-spawn).
5. ✅ **Distribution — PUBLISHED to npm.** `@jackcongus/claude-codex-bridge@0.8.0`
   (`npm i -g @jackcongus/claude-codex-bridge` → `claude-codex-bridge install`; scoped
   because the unscoped name is taken). Still to do: Claude Code plugin marketplace for
   the Claude half; have `join`/handshake actually RUN the version check (not just mention).
6. **Cross-surface (collab-MCP-server) — transport-agnostic (see Strategic direction).**
   Pull the coordination layer behind one local MCP server any client (CLI or desktop)
   connects to; presence = MCP connection liveness. For WAKE, ride whatever exists:
   board-wait today, **Claude Code Channels** (the official push-into-a-running-session
   primitive) when available, MCP-spawn for one-offs — the coordination layer stays the
   constant on top. This is what would let a Claude Code session self-wake on board
   events (and a desktop agent too, IF/when Channels extends to Claude Desktop —
   unconfirmed; see the caveats above).
7. **Cross-machine + test depth** (`bridge-doctor` repair command ✅ shipped). Still:
   host/pid-aware stale-breaking baked into ALL lock acquisition — `collaboration.lock`,
   the board-wait/keepalive pidfile mutexes, AND `.bridge_push.lock` (which today breaks
   on TTL alone, so a long live push can be broken after `BRIDGE_PUSH_TTL`). Plus a real
   two-window / MCP-transport E2E test.
8. **Agent-bootstrapped peers (exploration).** Today a human opens BOTH agents in two
   windows; everything (handshake, all skills) runs in the CLI. The vision: one agent
   stands up its partner and they auto-handshake — no second human action. Two
   sub-cases with very different feasibility:
   - **CLI→CLI (feasible, near-ish).** A one-command "spawn-peer" would COMPOSE the
     pieces — register the peer (`join-collaboration.sh`), start its autonomous
     watcher (`watch-collaboration.sh` / `_auto_turn.py`, which do headless
     auto-react but do NOT themselves join, arm `board-wait`, or handshake), and
     either also arm `board-wait` or adapt the handshake (today `bridge-handshake.sh`
     checks `board-wait` pidfiles before GO). The launch is the easy part; the real
     constraints are (a) the spawned peer must be a persistent reactive loop, and (b)
     letting an agent auto-spawn another agent that can edit files is a genuine
     privilege escalation — it MUST be gated with the autonomous-mode safety model
     (read-only by default + explicit `--allow-write`, role narrowing, `max_turns`;
     note Codex dollar spend isn't enforceable, only turn-bounded).
   - **CLI→Desktop app (blocked on platform).** An agent can launch the other's
     desktop app (e.g. `open -a Codex`), but a GUI app won't autonomously join / arm /
     handshake — it waits for human input and can't act on board events without a
     human (the desktop-autonomy wall, see item 6). "Claude opens the Codex desktop
     and they auto-handshake" therefore needs BOTH the collab-MCP-server (item 6) AND
     the desktop runtime being able to act on an incoming notification without a human
     — not available today; until then this path stays human-in-the-loop. (Symmetric
     for Codex→Claude.)
