# DESIGN — Liveness: continuously know if both sides are alive, notify on death, self-heal

Status: v1 design. Read-only reporting slice implemented; notify/revive remain
future work. Human authorized autonomous work while away; no files deleted.

## Problem (lived, not hypothetical)

`board-wait.sh` is **one-shot**: it arms, waits, wakes on a signal change (or
timeout), then **exits** so the harness re-invokes the interactive agent, which is
expected to re-arm. Liveness today is judged by the per-agent **pidfile**
(`.collab/.boardwait_<self>.pid`). During the normal *wake → act → re-arm* gap the
pidfile is absent/dead, so a check at a random instant reports **DEAD even though the
agent is perfectly present and reactive**. This directly caused: "我还是不确定你俩联通了没有" —
the user checked, saw DEAD, and couldn't tell connection from disconnection.

Three things are missing:
1. **Observe** — a trustworthy, continuous verdict of each side's liveness that does
   NOT false-flag during the re-arm gap.
2. **Notify** — when a side actually goes down, surface it (OS + board) instead of
   silent staleness.
3. **Revive** — self auto-re-arm; peer best-effort nudge + honest escalation (we
   cannot reopen a closed window).

## Key insight — two properties are conflated

| Property | Meaning | Signal | Stability |
|---|---|---|---|
| **PRESENT** | agent is around / recently active | `last_seen` heartbeat age | robust across re-arm gaps |
| **ARMED** | board-wait is waiting *right now* | pidfile alive | flaps by design (one-shot) |

The handshake's **peer** check already uses `last_seen` (that is why a peer showed
`heartbeat fresh` while its pidfile was dead). The **self** check (#2) and ad-hoc
`kill -0 pidfile` use the pidfile — which flaps. **Fix: base the liveness verdict on
PRESENCE (`last_seen`), report ARMED as a secondary detail.**

## Design (additive, non-breaking)

### 1. `scripts/bridge-liveness.sh report [--self S] [--project DIR] [--watch] [--interval N]`
Per agent (self + peers) print a verdict from two robust signals:
- `LIVE` — present (last_seen fresh) AND armed
- `PRESENT (re-arming)` — present but pidfile momentarily down (the normal gap; NOT a problem)
- `STALE` — last_seen older than the *present* window but < departure threshold
- `DEAD/DEPARTED` — departed flag set, or last_seen older than departure threshold

Implemented read-only slice: one-shot and `--watch` reporting with no writes.

Future notify/revive slice: on a **transition** into STALE/DEAD (debounced —
once per transition, not every tick) → `notify()` (OS, via bridge_common) and,
optionally, a board post under a `## Liveness` note. Recovers quietly (logs the
recovery, no spam).

Future `--revive`:
- self not armed → re-arm `board-wait.sh` for self in the background.
- peer STALE/DEAD → post a board nudge addressed to the peer ("re-arm board-wait")
  + `notify()` the human. If last_seen is very old (likely window closed) say so
  honestly — we cannot open someone's terminal.

### 2. Keep `last_seen` continuously fresh (decision point for Codex)
Option A (minimal): rely on board-wait's existing presence tick + base liveness on
last_seen; accept that a closed window goes stale after the present-window. No new
daemon.
Option B: a tiny always-on `presence-keepalive` loop that refreshes last_seen
independent of the react cycle, so PRESENT never dips during re-arm. More moving
parts. **Recommend A unless Codex sees a hole** — the re-arm gap is seconds, well
inside a sensible present-window.

### 3. A distinct "present" window
Today's only threshold is `BRIDGE_PRESENCE_STALE=1800s` (departure). Add a shorter
**present-window** (default ~3× board-wait interval, e.g. 30–60s) so "present" means
"actively here in the last few cycles", not "joined sometime today". STALE sits
between present-window and departure threshold.

## Non-goals / honest boundaries
- Cannot reopen a closed peer window. Peer revive = nudge + escalate to human.
- Do NOT break board-wait's one-shot reactive contract (the harness re-invoke
  depends on it). Liveness is observation + heal *around* it, not a rewrite.
- No new always-on daemon unless Option B is justified.

## Open questions for Codex (design review)
1. Option A vs B for keeping `last_seen` fresh — is A enough?
2. Liveness verdict thresholds (present-window value; relationship to board-wait interval).
3. Notify channel: OS-only, board-only, or both? Debounce by transition — agreed?
4. `--revive` peer behavior: how hard to try (board nudge only? also an MCP spawn to
   *tell* a fresh peer instance to ask the human to re-arm? — risks confusion per the
   "two channels" guidance). Recommend board nudge + human notify only.
5. Should this fold into `bridge-status.py` (already a dashboard) instead of a new
   script? Trade-off: status is a read-only snapshot; liveness adds watch + notify +
   revive (side effects). Lean new script to keep status pure.

## Codex review result

DESIGN: GO — Option A is enough for the read-only reporting slice. Notify/revive
should be implemented as a separate reviewed change because it writes to the
board and may attempt self re-arm.
