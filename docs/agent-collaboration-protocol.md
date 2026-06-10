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
