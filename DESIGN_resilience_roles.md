# DESIGN: Resilience & Role layer (v2)

Status: **DRAFT v2 — for Codex review.** v1 → REVISE; v2 folds in every hard
constraint Codex required and adopts the "roles foundation first" slicing. The
authoritative resolved constraints are in **"v2 hardening"** at the bottom; where
the prose above is looser, the hardening section governs. Builds on the shipped
autonomous watcher
(`DESIGN_autonomous_watcher.md` v4, SHIP). Adds four capabilities, all as
*pluggable role / failure behaviors on the same symmetric protocol*: role
transformation, self-repair (healer), self-improve (improver), and graceful
handoff on limits (coverer). The transport (MCP) and the turn protocol
(next_actor, global lock, CAS, high-water, draft→commit-under-lock, halt) are
UNCHANGED. Default posture stays safe; new power is opt-in.

## Unifying model: a role is (prompt template + output contract + permission profile)

Today both sides run the identical `_auto_turn.py` and the same draft contract —
the loop is symmetric and peer-to-peer. A **role** layers on top without touching
the protocol:

- `role` ∈ {peer, planner, executor, reviewer, healer, improver, coverer}.
- Each role selects: (a) a **prompt template** (what this agent is here to do),
  (b) an **output contract** (extra draft fields, e.g. reviewer adds `verdict`),
  (c) a **permission profile** (allowed tools; e.g. reviewer read-only, executor
  may `--allow-write`).
- Roles are assignable per project and **switchable mid-project**: an agent's
  draft may propose `role_change` for itself or the peer; the harness applies it
  under the lock at commit, like any other state change.

Role-specific communication (the answer to "does talk differ by role?"): the
TRANSPORT is identical; the MESSAGE SEMANTICS differ. planner→executor = task
spec (goal/constraints/acceptance). executor→reviewer = result + blockers.
reviewer→* = verdict + findings. The harness picks the template by the acting
agent's current role; `next_actor` ping-pong is unchanged.

Where roles live: a `roles` block in `collaboration_state.json`
(`{"Claude":{"role":"executor"},"Codex":{"role":"reviewer"}}`) so role state is
authoritative and lock-protected like the rest. Templates ship in
`templates/roles/<role>.md`; permission profiles default safe (read-only) unless
the watcher was started with `--allow-write`.

## Capability 1 — Role transformation

- Add `roles` to state + `templates/roles/*.md`.
- `_auto_turn.py` loads the acting agent's role, prepends that template to the
  turn prompt, and validates the role-specific output contract.
- A draft may include `role_change: {actor, role}`; applied under lock (CAS) at
  commit. Bounded: only roles in the known set; logged.

## Capability 2 — Self-repair (healer)

Today every anomaly → halt-to-awaiting_human. Add a **failure classification** and
a bounded **healer** path:

- **Recoverable** (transient): launch-level error (CLI failed to start), lock
  timeout, transient API/rate-limit (retry-after), a single malformed draft.
- **Fatal** (escalate to human): corrupt state, CAS conflict after retries,
  update_id regression, repeated failures over threshold, anything ambiguous.
- On a recoverable class, instead of halting:
  - mechanical first: bounded backoff retry (`max_repair_attempts`, default 2),
    matching the design's "launch-level transient retry."
  - if still failing, spawn a **healer-role turn**: it reads
    `collaboration_auto.log` + the `failure` record + relevant files, diagnoses,
    and either applies a **coordination-file-only** fix (never project/bridge code
    by default) or sets `awaiting_human` with a written diagnosis.
- Hard limits: healer is read-only + coordination-file writes; **never** edits the
  bridge's own scripts (that's the improver path, human-gated); never Bash;
  escalates when uncertain. All repair attempts counted and logged.

## Capability 3 — Self-improve (improver)

A periodic/triggered **improver-role** turn reads `collaboration_auto.log`
(recurring halts, slow turns, friction) + outcomes and produces an **improvement
proposal** — a patch or written recommendation for prompts / thresholds /
protocol / scripts.

- **Hard safety rule:** the improver NEVER auto-merges changes to the bridge's own
  code. It PROPOSES; the proposal goes through the **reviewer role (the peer) +
  human approval** — i.e. the exact "Claude improves / Codex reviews / human
  approves" loop this project has run all along, codified into roles.
- Auto-apply is permitted ONLY for explicitly-whitelisted **soft params** (prompt
  wording, numeric thresholds) and only when `--allow-self-tune` is set; core
  protocol/script edits are always human-gated.
- Output goes to an `improvements/` dir (proposals as files) + an "Improvement
  Proposals" board section; `next_actor` set to the reviewer, then human.

## Capability 4 — Graceful handoff on limits (coverer)

Keep the loop LIVE when one side degrades: **reroute instead of halt.**

Triggers & honest detection:
- `context_full` — the model self-reports in its draft (`handoff:{reason}`).
- `session_expiring` — harness tracks session age / catches a session-expired
  error.
- `account_block` / rate-limit — caught REACTIVELY as a 429/quota error. A hard
  block means that side often can't even write its own handoff note, so the
  ROBUST trigger is: **the peer notices N consecutive failures/silence from A and
  self-initiates covering.** (Self-report covers the soft cases; peer-detection
  covers the hard ones.)

Mechanism (reroute):
- On handoff for agent A: write a handoff note to A's outbox if A can still write;
  else the peer logs "A unreachable — covering."
- State: `next_actor = B`, `covering_for = "A"`, `handoff = {reason, since,
  original_role}`; `status` stays `active` (loop does NOT stop).
- B runs in **coverer** role: "you are covering for A; continue A's work using the
  board + digest; keep the project moving." B may assume A's role or handle both,
  bounded by `max_turns` and a separate `max_cover_turns`.
- **Handback:** when A returns (A's watcher fires a turn, actor==A while
  `covering_for==A`) or a human clears `covering_for`, A reclaims its role; B's
  coverage ends. A catches up by reading the board+digest.
- **Sustained coverage is feasible** precisely because each headless turn reads
  the *bounded* board + digest (the token-control layer): B's per-turn context
  stays O(small), so covering can last indefinitely without B itself filling up.

## Default posture

Safe by default, power opt-in: role permissions default read-only; healer can't
touch bridge code; improver proposes-not-merges; handoff/coverer and self-tune are
flags. All new behaviors are logged with their own event types
(`role_change`, `repair_attempt`, `improvement_proposed`, `handoff`, `covering`,
`handback`).

## v2 hardening (authoritative — resolves Codex's REVISE)

### Invariants that must never be violated (inherited from v4)
- Every role behavior still only produces a **draft**; ONLY the deterministic
  harness writes board/state/signal. No role/template may write coordination files.
- Permissions are enforced by the **harness**, not by prompt text. A prompt asking
  an agent to "be careful" is not a permission boundary.
- All role / handoff / handback / coverage transitions are **normal v4 commits**:
  global lock + CAS on `update_id` + signal-last + `update_id` increment. They
  **never** reset a watcher's high-water mark or write via a side channel.

### Implementation slicing (do NOT build all four at once)
1. **Slice 1 — roles foundation only:** `roles` block in state, `role_templates/`
   files, output-contract validation, and a deterministic `role_change` policy +
   logging. Ship this before anything else.
2. **Slice 2 — healer, mechanical retry only** (no agent healer yet).
3. **Slice 3 — coverer** with owner token + epoch.
4. **Slice 4 — improver** (last).
Each slice is its own design-review → implement → Codex-review pass.

### role_change policy (gated, monotonic)
- `self-downgrade` (to a less-privileged role, e.g. executor→reviewer): may apply
  directly under lock.
- `upgrade` (gaining write/privilege), `changing the PEER's role`, or `entering a
  special role (healer/improver/coverer)`: requires a reviewer-role approval turn
  or human gate, recorded in the decision log.
- **Permission monotonicity:** a role switch can never grant tools the watcher
  wasn't started with. `--allow-write` is the ceiling; roles only narrow from it.
- Persistent role lives in `roles`. Scoped overrides (healer/coverer) are
  per-period, carry `expires_at_update_id` / `remaining_turns`, and never pollute
  the persistent `roles` block.

### Failure taxonomy (fixed; improver may NOT loosen it)
- **Recoverable** (≤1 bounded retry): CLI launch failure, rate-limit w/
  retry-after, lock-unavailable, network timeout before the model starts, ONE
  malformed draft → exactly one deterministic re-prompt (not an autonomous
  ping-pong, not free-form healer).
- **Fatal → awaiting_human:** corrupt state/signal/high-water, CAS conflict after
  one re-read, update_id regression, repeated malformed drafts, cost-unknown while
  a cost cap is active, permission violation, model attempting a forbidden write.

### healer (Slice 2): propose-first, deterministic recipes only
- Default = diagnose + propose; escalate to `awaiting_human` when unsure.
- The ONLY auto-applied repairs are whitelisted **deterministic recipes**: break a
  provably-stale lock, rebuild a corrupt watcher high-water from state/signal when
  provably safe, append a diagnostic to the board.
- Healer must **never** change `update_id`, `next_actor`, `turn`, `cost_so_far`,
  or high-water except via a human-approved action. It enters through the normal
  queue (high-water/next_actor/CAS), never a side channel.

### improver (Slice 4): proposes; never edits bridge code
- Auto-tunable soft params (only with `--allow-self-tune`, whitelisted keys +
  bounded ranges, logged before/after, reversible): prompt wording, log verbosity,
  debounce latency, notification preference, board-rotation thresholds (within a
  range).
- **Immutable safety floor (never auto-tunable):** `max_turns`/`max_cost` upward,
  `LOCK_TTL` above cap, retry count above cap, the failure taxonomy, `--allow-*`
  flags, protocol fields, any script. This blocks an improver from gaming its own
  metrics (e.g. relabeling fatal→recoverable to cut halts).

### coverer / handoff (Slice 3): owner token, no impersonation, anti-flap
- State carries `coverage_owner`, `covered_actor`, `handoff_epoch`,
  `max_cover_turns` (default **2**), `handoff_cooldown_until`, `min_cover_turns`(1).
- The coverer **never impersonates A**. It writes a "Coverage Log (B covering A)"
  section, not A's outbox.
- **Detection / anti-flap:** B self-covers only after `failure_count[A] >= 2`
  within a window OR no progress for `2 × turn_timeout` while `next_actor==A` AND a
  turn-started log is absent (started-but-slow ≠ blocked). Cooldown prevents A/B
  flapping.
- **Handback:** A reclaims only via a normal committed turn under lock when
  `next_actor==A` and `handoff_epoch` matches; if A returns mid-B-turn, B's CAS
  sees the changed epoch/update_id and aborts safely. Human may clear coverage.
- After `max_cover_turns`, coverage stops at `awaiting_human` unless
  `--allow-cover-continue` is set. Continuous coverage requires that flag + a
  budget.

### Codex-side contract (any role)
Whether assigned reviewer / executor / healer / improver / coverer: Codex still
returns ONLY a JSON draft, never edits coordination files itself, and on any
permission / role / state inconsistency requests `awaiting_human` rather than
acting.
