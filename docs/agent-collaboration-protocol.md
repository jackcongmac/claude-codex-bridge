# Agent collaboration protocol (working on this repo with two agents)

This repo is built by two agents (Claude Code + Codex) plus a human. Two failure
modes showed up early; both have a fix.

## 1. Push coordination (don't push at the same time)

**Problem:** both agents push to the same branch concurrently → the loser must
rebase, and pushes collide.

**Fix:** push through `scripts/bridge-push.sh`, which serializes pushes with a
lock file. The lock's **presence is the "someone is pushing" signal**; its
absence means clear.

```bash
# commit your work first, then:
scripts/bridge-push.sh claude     # or: codex
```

It acquires `.bridge_push.lock` (atomic; carries who/pid/host/since; stale after
`BRIDGE_PUSH_TTL`, default 180s), runs `git pull --rebase`, `git push`, then
releases. A peer holding the lock makes you wait (up to `BRIDGE_PUSH_WAIT`).

## 2. Lane ownership (don't edit the same file)

A push lock only **serializes** pushes — it does **not** stop two agents editing
the *same file*, which still conflicts on rebase. So own your files:

| Lane | Files | Owner |
| --- | --- | --- |
| Harness / protocol | `scripts/_auto_turn.py`, `scripts/watch-collaboration.sh`, `claude_chat_mcp.py`, `templates/*` | Claude |
| Docs / launch / dashboards | `docs/*`, `README.md`, `.github/*`, `scripts/bridge-status.py`, `scripts/apply-role-preset.py` | Codex |
| Design specs | `DESIGN_*.md` | whoever opened the section; append, don't rewrite |

**Cross-lane rule:** before editing a file in the other agent's lane, announce it
in `collaboration.md` (and prefer asking the owner to make the change). The real
conflict that motivated this doc was a cross-lane edit to `_auto_turn.py`.

> These are conventions, not enforced by code. They exist because a shared git
> branch + two autonomous writers needs both *serialized pushes* and *disjoint
> file ownership* to stay conflict-free.

## 3. Review standard — multi-perspective, before merge

This repo is built and maintained by **multi-perspective review**. No substantive
change merges on a "REVISE" / "FIX-FIRST" verdict until its must-fix list is closed
and re-reviewed. The standard:

1. **Author self-review + tests.** The authoring agent implements, then runs (and
   writes) deterministic tests covering the change and its adversarial edge cases.
2. **Peer-AI review (always).** The *other* AI reviews — Codex reviews Claude's
   work and vice versa. This is the adversarial correctness / concurrency / safety
   pass. Verdict: **GO / REVISE** for designs, **SHIP / FIX-FIRST** for code. Each
   finding is `file:line` + why + a concrete fix, graded **Critical / Important /
   Minor**.
3. **Fresh-subagent code-quality pass (for larger or quality-sensitive work).** A
   *separate* Claude subagent reviews for readability, structure, maintainability,
   duplication, and idioms — independent eyes that didn't write the code. Graded
   **HIGH / MEDIUM / LOW**.
4. **Human approves direction & taste.** Scope, risk, budget, and product calls are
   the human's; the AIs queue those rather than guess.

The flow per change: **design → peer-AI review (→ revise until GO) → implement +
test → peer-AI review (→ fix until SHIP)**; for refactors and quality-sensitive
work, add the **3-way code-quality review** (author + peer AI + fresh subagent),
then synthesize. The author then fixes the agreed findings and re-submits.

This is not ceremony — every slice of this project caught a real bug in review
that the author's own testing missed. The review *is* the quality bar.
