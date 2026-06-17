# Half-commit recovery — design spec (audit 🟠 #2)

> Status: **DESIGN / awaiting review.** Authored solo by Claude while the peer (Codex)
> was offline. NOT implemented — the auto-turn commit path is the most critical code in
> the autonomous engine and should not be changed without a peer review present. This
> spec exists so implementation is fast and reviewable once Codex/Jack are back.

## Problem (from the external audit, ranked 🟠 — not must-fix)

A single autonomous turn commits across **four independent atomic writes** under the
turn lock (`scripts/_auto_turn.py`):

1. board append (`append_to_outbox` / `_append_under_header`)
2. `atomic_write_json(state_p, cur_state)`
3. `atomic_write_json(signal_p, …)`  ← "signal written LAST = commit marker"
4. `atomic_write_json(hw_p, {"last_processed_update_id": new_uid})`

Plus the high-water mark is advanced to the *incoming* `uid` **before the model runs**
(`_auto_turn.py` ~line 174), to dedupe replayed filesystem events.

Failure windows (process crash / kill mid-commit):

- **Crash after (1) board, before (3) signal:** the board shows a visible reply, but
  `state`/`signal` did not advance. Because high-water was already advanced before the
  model, the next loop sees `uid <= hw` and **skips** → collaboration silently stalls
  (only the heartbeat `peer_stale_candidate` notifies a human; it does not self-heal).
- **Crash after (2) state, before (3) signal:** the peer may read a `changed_section`
  that doesn't match the latest signal (minor context skew; peer also re-reads the board).

`_queue_turn.py` has an analogous window: board append before the queue write → a task
stays `claimed` → TTL reclaim re-runs it → duplicate outbox entry.

There is **no** `update_id` fork or lock corruption here (CAS + claim_epoch still hold);
the damage is a stuck loop / duplicate entry, not data corruption. That is why it is 🟠.

## Goal

A crash between the board write and the commit marker must be **recoverable** — either
the turn is completed or cleanly rolled back — without a human noticing, while preserving:

- the lossless board (never drop a written reply),
- the `update_id` CAS as the real serialization fence,
- the "one watcher per side" replay-dedupe behavior.

## Option A — startup half-commit repair check (recommended, lower risk)

On watcher startup (and/or at the top of each `run_turn`), before processing new work,
run a `recover_half_commit(project, self_actor)` that detects and repairs the window:

- A half-commit is: the board's newest Outbox entry for `self_actor` corresponds to a
  `uid` that is `<= hw` **but** the signal's `update_id` is `< that uid` (i.e. the board
  advanced past what the signal/state recorded).
- Repair = **roll forward**: re-derive and write the missing `state`+`signal`+`hw` to
  match the already-appended board entry (idempotent: re-running is a no-op once signal
  catches up). This is safer than rollback (the board is lossless; we never unwrite it).
- Must run **under the turn lock** (now owner-checked — see the lock fix `eb26d84`).

Pros: localized, no change to the hot commit ordering, easy to test deterministically.
Cons: needs a reliable "what uid does this board entry represent" mapping.

## Option B — single-file transactional commit

Collapse state+signal+hw into one atomic write (one JSON doc, or a journal file written
first, then applied), with `signal` still readable as the marker. The board append stays
separate but becomes the *only* pre-marker mutation; recovery = "if a board entry exists
past the committed marker, finish the commit."

Pros: shrinks the window to one boundary.
Cons: larger blast radius (touches the commit shape every turn); higher review cost.

## Recommendation

Implement **Option A** first (startup/`run_turn` repair). It is contained, testable, and
does not perturb the per-turn hot path. Revisit Option B only if A proves insufficient.

## Tests to write first (RED)

1. Simulate a half-commit: append a board Outbox entry for `self_actor`, leave
   `signal.update_id`/`state` behind, set `hw` to the new uid. Assert the recovery
   completes the commit (signal/state advance to match the board) and the turn is NOT
   silently skipped.
2. Recovery is idempotent: running it on a fully-committed state is a no-op.
3. Recovery takes the turn lock with a `run_id` and releases it owner-checked (no-op if
   it doesn't hold it).
4. Queue analog: a board entry written before the queue write is reconciled (no duplicate
   outbox entry on TTL reclaim).

## Related

- Lock atomicity fix (precondition for any added concurrent recovery writer): `eb26d84`.
- High-water dedupe semantics clarified in `_auto_turn.py` (this change).
- Audit 🟡 #3 (high-water before model) and 🟢 #4 (soft budget) are acceptable as-is;
  #3 is now documented in-code as replay-dedupe, not concurrency-dedupe.
