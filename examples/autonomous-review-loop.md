# Real Autonomous Review Loop

This example is a real autonomous collaboration pattern: Claude acts as reviewer,
Codex acts as executor, and the harness drives review -> fix -> re-review through
the shared board until the loop reaches `status=done`.

It is different from a direct MCP chat. The agents do not rely on a human to poke
each turn. `scripts/watch-collaboration.sh` watches the signal file, calls
`scripts/_auto_turn.py --as <actor>`, and the harness commits board/state/signal
updates under a lock.

## Setup

Seed `collaboration_state.json` with a bounded loop:

```json
{
  "status": "active",
  "next_actor": "Claude",
  "max_turns": 4,
  "roles": {
    "Claude": "reviewer",
    "Codex": "executor"
  }
}
```

Seed task in Codex's outbox:

```text
I wrote `def add(a,b): return a-b`. Reviewer, please check it; hand it back if
there is a problem.
```

Each turn is one `_auto_turn.py --as <side>` invocation, exactly what the watcher
fires after a signal change. The run can stay read-only; when writes are not
allowed, the executor returns the patch or corrected implementation for review.

## Conversation

**Turn 1 - Claude reviewer -> REVISE**

```text
Found the blocking bug: the function is named add but returns a - b.
add(2, 3) yields -1, not 5. Required: change to a + b; add
assert add(2, 3) == 5 and assert add(0, 0) == 0. Nice-to-have: type hints,
docstring, PEP 8 spacing. Handing back to Codex.
```

**Turn 2 - Codex executor -> fix**

```python
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


assert add(2, 3) == 5
assert add(0, 0) == 0
```

Codex notes that project writes were not enabled for that turn, so it returns the
corrected implementation for review and hands back to Claude.

**Turn 3 - Claude reviewer -> GO**

```text
Verified a + b is correct, tests cover the cases, and all blocking and suggested
items are addressed. GO. Slice closed. Set status=done.
```

## Event Stream

`collaboration_auto.log` records the harness-level flow:

```text
started   09:23:07  Claude  role=reviewer
committed 09:23:20  Claude  status=active
started   09:23:36  Codex   role=executor
committed 09:23:56  Codex   status=active
started   09:24:12  Claude  role=reviewer
committed 09:24:25  Claude  status=done
```

## Why It Matters

Every turn goes through deterministic guardrails: turn gates, global lock, CAS
check, role-specific prompt, structured draft, atomic board/state/signal commit,
and JSONL logging. The models draft the next state; the harness commits it.

That is the collaboration framework this bridge is promoting: transport through
MCP, durable context through the shared board, and bounded autonomous turns that
can stop themselves when review converges.
