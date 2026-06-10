# DESIGN: Autonomous coordination via signal-file watcher (v4)

Status: **DRAFT v4 — for Codex final review.** Not yet wired into the installer or
shipped scripts. v1 → REVISE, v2 → REVISE (board write outside lock), v3 → REVISE
(rotation clobbered the pending business signal). v4: rotation no longer touches
the signal / pending section, and the failure-policy text is aligned with
draft→commit-under-lock.

## Goal

Let Claude Code and Codex exchange updates **autonomously** — no human pinging
either agent — while spending **~zero tokens when idle** and staying **safe**
(bounded, cost-visible, no runaway loops, no double-processing). Event-driven.

## Non-goals / honest constraints

- Does NOT inject into a human's open interactive window. Autonomy runs
  **headless** agents (`claude -p`, `codex exec`) triggered by file events, and
  surfaces activity via a tail-able log + optional desktop notification.
- "Real-time" = seconds-latency event response, not instant in-window.

## Three files, three distinct jobs (was: one file — fixed per review)

| File | Job | Writer |
| --- | --- | --- |
| `collaboration.md` | Shared board (human-readable: outboxes, decisions) | both agents |
| `collaboration_signal.json` | **Event marker only**: "the board just changed" | whoever wrote the board |
| `collaboration_state.json` | **Authoritative control state** for the auto-loop | whoever holds the global write lock |

`collaboration_signal.json` (event):

```json
{
  "update_id": 7,
  "updated_at": "2026-06-09 21:00:00 PDT",
  "updated_by": "Codex",
  "changed_section": "Codex Outbox",
  "summary": "Codex finished X, asks Claude to QA."
}
```

`collaboration_state.json` (authority):

```json
{
  "status": "active",            // active | awaiting_human | done | paused
  "next_actor": "Claude",        // Claude | Codex | human | null  (who is AUTHORIZED next)
  "turn": 4,
  "max_turns": 12,
  "cost_so_far_usd": 0.83,
  "max_cost_usd": 5.0,
  "run_id": "run-2026-06-09-2100",
  "last_writer": "Codex",
  "last_update_id": 7,           // update_id of the last committed board write
  "failure": null                // or {reason, actor, update_id} when halted
}
```

Back-compat: if `collaboration_state.json` is absent, autonomous mode is OFF
(manual bridge still works); watchers refuse to run without it.

## Concurrency model (hardened)

### 1. Single global write lock — not per-side

One lock `collaboration.lock` guards **all** writes to signal + state + board by
the auto-loop. Lock carries `{pid, host, run_id, acquired_at}`. **Stale-lock
handling:** if `acquired_at` older than `LOCK_TTL` (default 10 min) AND the pid is
not alive, the lock may be broken (logged). Otherwise wait/skip.

### 2. Whose-turn token (`next_actor`) — authority, not history

`updated_by` only records who wrote last. A watcher fires its agent **only if
`state.next_actor == self`**. After a turn, the agent sets
`next_actor` to the other agent, or to `human`/`null` to stop. This — not
`updated_by` — prevents both sides thinking it's their turn.

### 3. Per-watcher high-water mark — kills double-processing

Each watcher persists `.watcher_<side>.state` = `{ last_processed_update_id }`.
A turn fires only when **ALL** hold:

- `signal.update_id > last_processed_update_id` (new event, not a replayed
  fs-event / rename / editor save-storm), AND
- `state.next_actor == self`, AND
- `state.status == "active"`, AND
- `state.turn < state.max_turns`, AND
- `state.cost_so_far_usd < state.max_cost_usd`, AND
- global lock acquirable.

On firing, immediately advance `last_processed_update_id = signal.update_id`
(even before the agent finishes) so retries/duplicate events can't re-consume it.

### 4. Draft → commit-under-lock (the board is NEVER written outside the lock)

The global lock guards **all three** files — board + state + signal. The agent
does its thinking/output as a private draft FIRST, and only mutates
`collaboration.md` inside the lock. Sequence:

1. Read `signal.update_id` → call it `seen`. Read the needed board section(s).
2. Do the work and produce output as a **private draft/patch** (e.g.
   `.turn_<run_id>.draft`). Do NOT touch `collaboration.md` yet.
3. **Acquire the global lock.**
4. Re-read signal. **If `signal.update_id != seen`** → CAS conflict (someone
   committed concurrently). Still holding the lock, write `status=awaiting_human`,
   `failure={reason:"cas_conflict", seen, now}` to state; release lock; notify;
   stop. No board change was made — the draft is discarded.
5. **Else, all inside the lock:** merge the draft into `collaboration.md` (append
   to this agent's outbox / decision log) → atomically write
   `collaboration_state.json` (turn+1, next_actor=other, cost+=this turn,
   last_update_id=seen+1) → atomically write `collaboration_signal.json` **last**
   (update_id=seen+1). Then release the lock.

Because the board is mutated only in step 5 under the lock, two agents can never
concurrently write it, and a CAS loser (step 4) never touched it. Atomic write =
`*.tmp` + `rename()` (atomic on POSIX). Signal written **last** = commit marker,
so any watcher that fires sees a consistent board + state. **All failure-state
writes (incl. CAS conflict, errors) happen under the lock, never lock-free.**

## Failure / timeout policy (was: undefined — fixed)

Default = **halt to `awaiting_human`** and notify the human. Specifically:

- Agent timeout, non-zero exit, JSON parse failure, **cost field unparseable**,
  corrupt signal/state, CAS conflict, update_id regression (manual edit lowered
  it), stale-lock ambiguity → write `status=awaiting_human` + `failure={...}`,
  stop, notify. Never auto-continue past an error.
- The **only** retry allowed: a single short backoff for *launch-level* transient
  errors (watcher/CLI failed to start), NOT for errors after the agent began
  executing (it may have produced a partial private draft — never a board write).
- Partial-write guard: because the board is only ever mutated inside the lock at
  the final commit (step 5), a turn that dies earlier leaves at most a discarded
  private draft — `collaboration.md` was never committed and `update_id` is
  unchanged, so the next watcher sees no new event and does nothing. The orphan
  draft is logged (and cleaned up); the human is notified. No half-written board.

## Cost accounting (Codex-exec caveat called out)

- Claude side: parse `total_cost_usd` from `claude -p --output-format json`.
- **Codex side: `codex exec` cost-field parseability is UNCONFIRMED.** Until
  verified, the Codex watcher must NOT rely on per-turn $ for its ceiling; it
  enforces `max_turns` (always available) and treats unparseable cost as a
  **halt-to-awaiting_human** condition rather than "continue". (Implementation
  step 0: probe `codex exec` output for a cost field; document the finding.)
- Both caps (`max_turns`, `max_cost_usd`) are checked **twice**: before launching
  a turn, and again before writing back.

## Safety defaults

- **Opt-in only.** Off unless the human starts the watcher(s). README states
  plainly: this spends money with no human in the loop.
- **Read-only by default.** Headless agents may write ONLY the coordination files
  (`collaboration.md`, signal, state); project-file writes require `--allow-write`
  (logged at run start). Never `Bash`.
- **Visible.** Every turn appends to `collaboration_auto.log`:
  `run_id, update_id, actor, next_actor, status, tokens, cost, exit_code, ts`.
  Log rotates at `LOG_MAX_BYTES` (default 5 MB). Optional `osascript` /
  `notify-send` on halt / awaiting_human.
- **Kill switches.** Ctrl-C the watcher; or set `status=paused`/`done`; resume by
  setting `active` + bumping signal.

## Scaling the board for large projects (token control)

Problem: on a long/large project `collaboration.md` grows unbounded, so reading it
each turn wastes tokens. Minimal solution = three cheap mechanisms, ordered by
impact; the first costs nothing new.

1. **Read only what changed (already in the protocol).** The signal's
   `changed_section` lets an agent read just that section per turn, not the whole
   board. This is the primary saver. Requirement: the board stays
   section-addressable (stable `## ` headers) so a single section can be sliced
   out cheaply.

2. **Bounded active board + lossless archive rotation.** Keep `collaboration.md`
   small: it holds `## Digest`, Current Task, Open Questions, File Locks, and only
   the most recent **K entries** per Outbox / Decision Log. When the board crosses
   a threshold (`BOARD_MAX_BYTES`, default ~64 KB, or K entries), older entries are
   **appended** to `collaboration_archive/collaboration_<YYYY-MM>.md` and removed
   from the active board, which keeps a one-line pointer (`older → archive/…`).
   Archive is lossless and read only on demand. Per-turn read stays O(small).

3. **Digest (optional compaction).** A short `## Digest` block at the top of the
   active board = a running summary of resolved/closed context (done items, key
   decisions). Refreshed at rotation time by an agent summarizing what was
   archived. Lossy-but-cheap; the full history remains in the archive. Opt-in.

**Default posture:** archive rotation (lossless) is the floor; digest refresh is
optional. Don't summarize aggressively — archive preserves fidelity, digest only
gives the gist.

**Rotation must NOT touch the business signal, and must NOT archive a pending
section.** Compaction rewrites `collaboration.md`, so it runs **under the global
lock** — but unlike an agent turn it does the following:

- It **does NOT write `collaboration_signal.json` and does NOT bump `update_id`.**
  (Writing the signal would overwrite a still-pending business event: e.g. after
  Claude commits `update_id=7, next_actor=Codex`, if the compactor wrote
  `update_id=8, updated_by=system` before the Codex watcher fired, that watcher
  would see only the `system` signal and the Claude→Codex turn would be lost.) The
  business signal + per-watcher high-water mark are left untouched, so no pending
  turn can be dropped. Rotation records itself only in `collaboration_auto.log`.
- It **must not archive the section referenced by the current pending signal.**
  Before moving content, it reads `collaboration_signal.json`; the section named
  by `changed_section` of the latest (possibly unconsumed) update is **excluded
  from archiving** so an agent reading that section by name still finds it.
- It still acquires the global lock and respects atomic writes; it does **not**
  change `next_actor` or increment `turn`.
- Safest scheduling (recommended default): run rotation only when there is no
  pending turn — `state.status != "active"` or `state.next_actor in ["human", null]`
  — which sidesteps the pending-section question entirely.

Net: per-turn token cost ≈ (changed section + small digest), independent of total
project history.

| File | Role |
| --- | --- |
| `scripts/watch-collaboration.sh` | Watcher. `--as claude|codex`, `--allow-write`, `--max-turns`, `--max-cost`, `--lock-ttl`. fswatch (macOS) / inotifywait (Linux). Implements all gates above. |
| `scripts/_auto_turn.py` | Helper (stdlib only): read signal+state, evaluate the firing gates, advance high-water mark, run the headless agent, parse cost/exit, CAS-commit signal+state atomically under the global lock, append to log, enforce caps + failure policy. |
| `templates/collaboration_state.json` | Starter authoritative-state file. |
| `scripts/compact-collaboration.sh` | Lossless archive rotation when the board crosses `BOARD_MAX_BYTES`/K entries; optional digest refresh. Runs under the global lock, tagged maintenance (does not trigger a turn). |
| README / SKILL "Autonomous mode" + "Scaling" | Start watchers, control protocol, costs, safety, kill switches, board rotation. |

## Codex-side contract (explicit, per review)

When the Codex watcher fires a `codex exec` turn, the Codex turn MUST:

1. Run only if `state.next_actor == "Codex"` and `signal.update_id` is new.
2. Read only the `changed_section` named in the signal (+ minimal needed context).
3. Not modify project files unless `--allow-write` was set; coordination files OK.
4. On finish: prepare output as a **private draft**; then **under the global
   lock**, CAS-check `update_id`, merge the draft into the board, write state
   (`last_writer="Codex"`, `next_actor="Claude"` or `human`/`done`, `turn+1`),
   write signal **last**. On any anomaly / CAS conflict, write
   `status="awaiting_human"` under the lock instead and stop. **Never mutate the
   board outside the lock.**

## Resolved review items (v1 → v2)

- Control state split into `collaboration_state.json` (Q1). ✓
- Per-watcher `last_processed_update_id` high-water mark (Q2). ✓
- Explicit `next_actor` + global write lock + CAS (Q3). ✓
- Failure/timeout → halt-to-awaiting_human, single launch-level retry only (Q4). ✓
- Read-only default; project writes opt-in (Q5). ✓
- Extra runaway/corruption modes covered: duplicate update_id, concurrent
  overwrite, cost-parse failure, stale lock, watcher-restart amnesia,
  signal-last violation, update_id regression, log growth (Q6). ✓
- Stray trailing code fence removed. ✓
