# DRAFT (Claude → Codex handoff): real autonomous-run demo for examples/

Hand-off for Codex to polish into `examples/` (e.g. `examples/autonomous-review-loop.md`)
and link from the README as the headline demo, then delete this draft. This is a
REAL run (real Claude + real Codex turns, driven by the harness), not a mock.

## What this shows

Two agents collaborating autonomously on a shared board: Claude (reviewer) and
Codex (executor) take turns through the harness — review → fix → re-review — and
the loop **self-terminates** at `status=done` when the work is correct. No human
in the loop between turns.

## Setup

```json
// collaboration_state.json
{ "status": "active", "next_actor": "Claude", "max_turns": 4,
  "roles": { "Claude": "reviewer", "Codex": "executor" } }
```

Seed task (in Codex's outbox): *"I wrote `def add(a,b): return a-b`. Reviewer,
please check it; hand it back if there's a problem."*

Each turn is one `_auto_turn.py --as <side>` (exactly what the watcher fires on a
signal change), read-only (`allow_project_writes:false`).

## The conversation (verbatim)

**Turn 1 — Claude (reviewer) → REVISE**
> Found the blocking bug: the function is named `add` but returns `a - b`.
> `add(2,3)` yields `-1`, not `5`. Required: change to `a + b`; add
> `assert add(2,3)==5` / `assert add(0,0)==0`. Nice-to-have: type hints, docstring,
> PEP 8 spacing. Handing back to Codex.

**Turn 2 — Codex (executor) → fix**
> ```python
> def add(a: int, b: int) -> int:
>     """Return the sum of two integers."""
>     return a + b
>
> assert add(2, 3) == 5
> assert add(0, 0) == 0
> ```
> (Noted it had no project-write permission this turn, so it returned the
> corrected implementation for review.) Handing back to Claude.

**Turn 3 — Claude (reviewer) → GO**
> Verified `a + b` is correct, tests cover the cases, all blocking + suggested
> items addressed. **GO.** Slice closed. → `status=done`.

## The event stream (harness log)

```
started   09:23:07  Claude  role=reviewer
committed 09:23:20  Claude  status=active
started   09:23:36  Codex   role=executor
committed 09:23:56  Codex   status=active
started   09:24:12  Claude  role=reviewer
committed 09:24:25  Claude  status=done     ← loop self-terminates
```

## Why it matters

Every turn went through the deterministic harness: turn gates, global lock, CAS,
role-specific prompt + output contract, atomic board/state/signal commit, and the
JSONL log. The agents never wrote the protocol files themselves — they only
produced drafts; the harness committed them. The loop converged and stopped on its
own at `done`. This is the review → execute → re-review pattern the bridge exists
to enable, running unattended end-to-end.

(Codex: a short asciinema/GIF of `tail -f collaboration_auto.log` during a run
would make a great README hero; the transcript above is the substance.)
