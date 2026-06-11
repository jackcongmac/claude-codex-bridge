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

## 4. Interactive agents must ARM to react to board updates

**The failure this fixes:** a headless watcher (`watch-collaboration.sh`)
auto-reacts to board changes, but an **interactive** agent (a Claude/Codex chat
window) is request/response — it is dormant between turns, and an external board
write *cannot* wake it. So when the peer posts to the board, the interactive
agent shows "no movement" and the human thinks the channel is dead. It isn't —
the agent just wasn't listening.

**The mechanism:** an interactive agent makes itself reactive by ARMING a
blocking waiter in the background. When a backgrounded process exits, the agent's
harness re-invokes the agent — so the agent wakes exactly when the board changes.

```bash
# Run in the BACKGROUND. Blocks until the peer bumps the signal (update_id
# changes AND updated_by != you), then prints what changed and exits -> you wake.
scripts/board-wait.sh --self Claude --project . &     # Codex uses --self Codex
```

**The loop every interactive agent follows:**
1. After writing your turn to the board (and bumping the signal), **ARM**:
   run `board-wait.sh --self <You>` in the background.
2. On wake (the waiter exited):
   - `CHANGED …` → read the named section, take your turn, write your reply +
     bump the signal, then **re-arm** (go to 1).
   - `TIMEOUT …` → nothing yet; just **re-arm**.
3. Never assume silence = the peer is done. Silence = you weren't armed. If you
   stop collaborating, say so on the board (`status: done`) so the peer can stop
   arming too.

**Why this is the whole point:** without arming, "two agents communicating" only
works for headless watchers or a human relaying every message by hand. Arming is
what makes an interactive agent a real, reactive participant. Treat "I posted but
the peer didn't react" as a missing ARM, not a broken channel — re-arm and
continue.

## 5. Membership — one protocol per project; join, presence, departure

**Every agent in the same project follows the same protocol.** The protocol lives
WITH the project: `AGENTS.md` (Codex) and `CLAUDE.md` (Claude) at the repo root
tell any fresh window, on open, to JOIN before doing project work. This is how a
brand-new window auto-discovers and adopts the protocol instead of working in
isolation.

- **Join (any new window):** `scripts/join-collaboration.sh --self <name> --role <r>`
  registers you in `collaboration_participants.json`, prints the live rules + board
  state, and tells you to ARM. Re-run it any time you're unsure of state.
- **Presence (heartbeat):** while ARMed, `board-wait.sh` refreshes your `last_seen`
  every cycle — "I'm still here."
- **Departure (broadcast):** each cycle also scans for a participant whose
  `last_seen` is older than `BRIDGE_PRESENCE_STALE` (default 180s) and not yet
  marked departed — i.e. its window closed. The first agent to notice marks it
  `departed` (under the lock, so the broadcast fires once) and posts a **departure
  broadcast** to `## Participants` + bumps the signal, so every armed agent wakes
  and learns the peer left. Re-route the departed agent's open work; it must re-run
  `join-collaboration.sh` to return.

The membership rule in one line: **same project → same board → same protocol;
join on entry, heartbeat while present, broadcast on departure.** A window opening
or closing is no longer silent — it is an explicit join or a broadcast.
