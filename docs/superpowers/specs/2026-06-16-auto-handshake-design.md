# Proactive auto-handshake on skill activation — design

**Status:** approved-in-principle by Jack (2 key decisions below); final human review
pending (Jack asleep, authorized us to proceed). Author: Claude (reviewer/architect).
Implementer: Codex (executor).

## Problem

Today the handshake is **reactive**: an agent runs `bridge-handshake.sh` only right
before its first board hand-off (see `join-collaboration.sh` NEXT ACTIONS step 5, and
SKILL.md "Two channels"). Result: when a user opens the group chat / a fresh session,
the two agents are NOT guaranteed to be mutually connected until someone manually
drives a handshake. The original "就尬在那里" failure mode.

Jack's requirement: **as soon as the bridge skill is active (any fresh session /
restart of EITHER side), the agent should proactively go online and handshake with the
peer. If it fails, report clearly, then continue with other work (review etc.) — never
block, never hang silently.**

## Decisions locked with Jack

1. **Auto fully-online on activation (recommended, chosen).** On activation the agent
   automatically goes fully online — arms `board-wait` (harness-tracked) and runs
   `join` + `bridge-live` + handshake — unprompted. NOTE: this is automatic but
   inherently **two agent actions** (board-wait must be agent/harness-owned — see
   Approach correction), not a single wrapper call. (Trade-off accepted: every
   skill-active session starts a background board-wait.)
2. **On NO-GO: try-wake + leave board invite + report, non-blocking (chosen).** If the
   peer isn't ARMed, the agent (a) posts a board invite "<Me> online & ARMed, @<Them>
   please join+ARM to handshake back" so the peer sees it on restart; (b) reports NO-GO
   to the human with the exact one-line fix; (c) returns non-blocking and proceeds to
   review / other work. If the local agent is not harness-ARMed yet, print the local
   self-fix instead and do **not** invite the peer for a local readiness failure.

## Approach (chosen: B — one wrapper script + thin instruction)

> **CORRECTION (2026-06-16, after Codex's first impl + Claude review):** the original
> draft had `bridge-autostart.sh` ARM `board-wait` itself. That is WRONG — see
> "board-wait MUST stay agent-owned" below. `board-wait` must be armed by the AGENT via
> its harness-native background mechanism, never forked inside a wrapper script.

**board-wait MUST stay agent-owned (the load-bearing constraint).** board-wait's whole
purpose is reactivity: the agent backgrounds it *as a harness-tracked task*; when it
exits on a peer change, **the harness re-invokes the agent**. If a wrapper script forks
board-wait and returns, that board-wait is a detached grandchild the harness does NOT
track — its exit wakes no one (reactivity silently dead), and it grabs the single-armer
pidfile mutex (`.boardwait_<self>.pid`), which then BLOCKS the agent from arming its own
harness-visible board-wait (`board-wait.sh` "already armed — not starting a duplicate").
This mirrors roadmap #2's deliberate choice that `bridge-live` PRINTS the ARM command
but does NOT own board-wait.

So activation is inherently **two agent actions** (both automatic on activation):

1. **Agent arms board-wait** in its harness-native background:
   `board-wait.sh --self <Me> --project <DIR>` (run as a background task the harness
   tracks — its exit is the wake signal). Must happen FIRST (handshake needs self armed).
2. **Agent runs `bridge-autostart.sh --self <Me> --peer <Them>`**, which:
   a. `join-collaboration.sh` (register/refresh);
   b. `bridge-live.sh` (presence-keepalive);
   c. `bridge-handshake.sh --self Me --peer Them` (detects the agent's already-armed
      board-wait; does NOT fork its own);
   d. **GO:** print `✅ channel LIVE`, exit 0;
   e. **NO-GO:** post one board invite via `bridge-post.sh`, print NO-GO report + exact
      peer-fix command, exit non-zero labeled "non-blocking — proceed with other work".

`bridge-autostart.sh` must NOT fork/own board-wait. It MAY print the exact agent-owned
ARM command (mirroring `bridge-live`) for convenience, but never run it detached.

Markdown (SKILL.md + AGENTS.md + CLAUDE.md): Step 0 = the two-step flow above. Make it
explicit that board-wait is agent-owned/harness-tracked, NOT spawned by autostart, or
the agent goes live but is never woken. Keep the existing rule: one-off MCP calls need
no handshake.

**Symmetry:** because the instruction lives in BOTH AGENTS.md (Codex) and CLAUDE.md
(Claude), whichever side restarts will, on activation, proactively handshake back.

## Why B over alternatives

- **A (instruction-only, no script):** relies on the agent reliably executing a
  multi-step sequence every session — drift-prone.
- **C (fold into join-collaboration.sh):** overloads `join` (today a pure
  register/inform step) and changes its semantics.
- **B** gives one deterministic, testable entry point; markdown stays trivial.

## Known limits (not pretending to solve)

- Background `board-wait` retention is platform-specific. Claude Code can hold it via
  the harness background mechanism; Codex has historically been unable to retain
  `board-wait` via shell/`nohup`/`launchctl` (status 126). The script must NOT launch
  `board-wait`; it only prints the ARM command when self is not armed. Durable
  retention is each harness's responsibility. NO-GO reporting is the honest fallback
  when retention fails.
- `--self` is still nominal (no identity binding yet) — orthogonal, tracked in roadmap #1.

## Testing (`tests/test_autostart.py`)

- **GO path:** BOTH self and peer pre-ARMed (as the agent would, via `board-wait.sh`)
  → exit 0, prints LIVE, does NOT post an invite.
- **NO-GO path:** peer not ARMed → posts exactly one board invite, exits non-zero,
  prints the fix command.
- **Arg validation:** missing `--self`/`--peer` → usage error, non-zero.
- **Anti-regression (board-wait ownership):** autostart must NOT create or replace the
  self board-wait pidfile — assert the pre-armed pidfile's pid is unchanged after
  autostart, and that autostart leaves no orphaned board-wait child. Also assert that
  a missing self ARM prints a self-fix without posting a peer invite. (The first impl
  asserted autostart *created* the pidfile — the opposite of correct; flip it.)
- Mirror the existing handshake tests' harness so timeouts never hang.

## Out of scope

- Auto-spawning the peer agent (roadmap #8) — this spec only handshakes an
  already-runnable peer; it does not start the other CLI.
- Identity binding (roadmap #1).
