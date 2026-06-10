# DESIGN: Multi-agent scalability (N agents per AI) — v1

Status: **DRAFT v1 — for Codex review.** A deliberate v2 architecture, not a patch.
Today's protocol assumes exactly TWO actors (one Claude, one Codex) taking
SEQUENTIAL turns via a single `next_actor` token, a single global lock, and a
single board. This doc designs how to scale to **multiple agents per AI**
(e.g. claude-planner + claude-rev-1 + codex-exec-1 + codex-exec-2) on a large
project.

## What breaks today (and what doesn't)

- **Safety holds.** The global lock (O_EXCL) + CAS already serialize N writers:
  concurrent commits → CAS conflict → loser halts. No corruption even with many
  agents. The lock primitive is the RIGHT foundation; only the coordination layer
  on top must change.
- **`next_actor` is binary** → with 3 claude agents, "next_actor=Claude" doesn't
  say WHICH one. All three act; lock+CAS let only one commit; the other two waste
  a model call + halt.
- **Per-side state clobber** → `.watcher_claude.state` (high-water) /
  `.watcher_claude.session` are keyed by SIDE; multiple claude agents overwrite
  each other's dedup mark + session.
- **Single global lock = throughput bottleneck** → "parallel" agents serialize
  through one lock.
- **The protocol is sequential by construction** → ping-pong assumes one-at-a-time.

## The shift: token → identity + task-claim queue + sharding

### 1. Agent identity
Each agent has a unique `agent_id` (`claude-planner`, `codex-exec-2`, …). The
watcher is started `--as claude|codex --agent-id <id>`. State is keyed by
`agent_id`, not side: `.watcher_<agent_id>.state`, `.watcher_<agent_id>.session`.

### 2. Work-queue replaces `next_actor`
A `collaboration_queue.json` holds tasks:
```json
{ "tasks": [
  { "id": "t-12", "title": "review draft X", "needs_role": "reviewer",
    "needs_side": "claude", "status": "open",
    "claimed_by": null, "claimed_at": null, "depends_on": ["t-9"],
    "created_by": "codex-exec-1" } ] }
```
`status ∈ open | claimed | done | failed`. An agent doesn't wait for a token; it
looks for an OPEN task it's eligible for (`needs_role`/`needs_side`/capability) with
all `depends_on` done, and CLAIMS it. Multiple agents claim DIFFERENT tasks → real
parallelism.

### 3. Claim is the coordination primitive (lock held briefly)
Per turn:
1. Acquire lock → re-read queue → pick an eligible OPEN task whose deps are done →
   set `status=claimed, claimed_by=self, claimed_at=now` → release lock. (Claim is
   a tiny, fast critical section.)
2. Do the work with NO lock held (the slow model run).
3. Acquire lock → CAS (task still `claimed_by==self`?) → write the agent's output
   (to its own `## <agent_id> Outbox` / a task-result section), set `status=done`,
   enqueue any follow-up tasks → release lock.
Two agents can never run the same task: the claim is under the lock.

### 4. Dead-agent recovery (claim TTL)
A `claimed` task whose `claimed_at` is older than `CLAIM_TTL` and whose claimer is
not making progress is reclaimable (mirrors the stale-lock break): another eligible
agent may re-open it (logged). Prevents a crashed agent from stalling a task
forever.

### 5. Sharding (reduce contention as agents grow)
- The single global lock is held only during claim + commit (not during model
  runs), so contention stays low for a handful of agents.
- For larger fleets / big projects: shard by **workstream** — multiple boards +
  queues + locks (`collaboration/<stream>/…`). Agents on different streams never
  contend. The queue can carry a `stream` field; a watcher subscribes to one or
  more streams.

### 6. Board model
Each agent writes its OWN `## <agent_id> Outbox` (no shared-section contention).
Compaction (already built) archives old entries. The board can also shard per
stream.

## Safety invariants (carried over from v4)
- All queue/board/state writes under the lock, atomic (`tmp`+rename), CAS-checked.
- Per-`agent_id` high-water + session (no clobber).
- A task is acted on by exactly one agent (claim under lock); claim TTL handles
  death.
- Budgets (`max_turns`/`max_cost`) become per-project (and optionally per-agent).

## Backward compatibility
The 2-actor model is the degenerate case: a queue with a single open task whose
`needs_side` alternates = today's ping-pong. Recommend the queue model SUPERSEDES
`next_actor` but ships behind a flag (`--queue-mode`), so existing 2-actor setups
keep working unchanged until they opt in.

## Open questions for Codex
1. Queue location: a separate `collaboration_queue.json` (own lock/CAS) vs a
   section in `collaboration_state.json`? Trade-offs for contention + atomicity.
2. Lock granularity for MVP: keep ONE global lock (brief holds) or go straight to
   per-stream locks? Where's the knee where one lock stops scaling?
3. Claim TTL + reclaim: how to distinguish "slow agent still working" from "dead
   agent" without a heartbeat? Require claimed tasks to renew `claimed_at`
   periodically?
4. Dependencies (`depends_on`): enforce in the claim gate (task not claimable until
   deps `done`). Cycle detection needed?
5. Fairness/starvation: first-to-claim-under-lock — does a fast agent starve a
   slow one? Need priority or round-robin among eligible agents?
6. Signal/high-water with a queue: keep the single `collaboration_signal.json`
   (bump on any queue/board change) so watchers wake, but dedup per agent on
   `update_id`? Or per-task events?
7. Output contract: do we keep the draft→commit-under-lock + the harness writing
   files, now per-task? (Yes, I think — same invariant.)
8. Is `--queue-mode` behind a flag the right migration, or should multi-agent be a
   separate tool/profile to avoid complicating the 2-actor happy path?
9. Anything that lets N agents corrupt the queue/board or double-act that the
   lock+CAS+claim-TTL don't already cover.

## v1 refinements (per Codex joint review — REVISE → design-first)

Status note: verdict is **REVISE into a refined design, NOT implement yet.** Both
AIs agree the direction is right but multi-agent is a deliberate v2. Hard
constraints to fold in before any implementation:

- **"Safe" = files-not-corrupted, NOT no-duplicate-execution.** With several
  same-side agents, all may spend a model call and only one commits. The whole
  point of the claim model is to STOP that waste up front (claim before running).
- **Fencing token (`claim_epoch`).** Every claim/renew/commit/reclaim must check
  `claimed_by==self && claim_epoch==seen_epoch`. A TTL-reclaimed task gets a new
  epoch, so a late zombie agent's commit is fenced off.
- **Lease renewal, not just `claimed_at`.** Task carries
  `lease_expires_at` + `heartbeat_at`; a long model task renews its lease
  periodically. TTL reclaims only a lease that EXPIRED without renewal — that's how
  you distinguish "slow but alive" from "dead". (No heartbeat → can't tell.)
- **Idempotency for enqueue.** Follow-up task creation needs a deterministic id /
  `idempotency_key` so a retried commit doesn't create duplicate tasks.
- **`agent_id` is a sanitized token** (restricted charset). Every per-agent
  filename (`.watcher_<agent_id>.state`) must be sanitized — no path injection.
- **depends_on gate** rejects self-deps / missing deps at creation; a detected
  cycle → `failed`/`awaiting_human`, never an infinite claim no-op.
- **Fairness MVP**: `priority` + FIFO (`created_at`) + `attempts`; soft cap of one
  claim per agent per heartbeat. Round-robin later.
- **Queue lives in its own `collaboration_queue.json`** (not in state — state is
  the low-churn 2-actor control plane; the queue is high-churn).
- **MVP keeps ONE global lock** (claim/commit critical sections are short → fine
  for ~4–8 agents). Per-stream locks + multi-board sharding are **v2b**.
- **Separate harness `_queue_turn.py`** behind `--queue-mode`; do NOT bloat
  `_auto_turn.py` with branches. The 2-actor happy path stays untouched.
- Output contract unchanged: model produces a task-result draft; the harness
  commits under the lock; an agent writes only its own `## <agent_id> Outbox`.

### Minimal viable slice (when/if we build it)
1. `--queue-mode` → `_queue_turn.py`. 2. `collaboration_queue.json` + the existing
global lock. 3. per-`agent_id` `.watcher_<agent_id>.state/session`. 4. claim one
eligible task → run model → commit result → done/enqueue. 5. lease renewal +
`claim_epoch` fencing. 6. priority/FIFO only — no sharding, no multi-board, no
round-robin. This validates "N agents don't double-execute / don't clobber / don't
burn duplicate model calls" before investing in sharding.
