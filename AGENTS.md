# For any AI agent working in this repo (Codex reads this; Claude reads CLAUDE.md)

**This is a multi-agent collaboration project. Before doing project work, JOIN the
collaboration protocol — do not work in isolation.**

## Step 0 — Join (run this first, every fresh session)

```bash
scripts/join-collaboration.sh --self <YourName> --role <peer|planner|executor|reviewer>
```

(`<YourName>` is your stable agent id this session, e.g. `Codex`, `codex-exec-1`,
`Claude`, `claude-rev`.) The script prints the live rules, registers you in the
participants list, shows you the current board state, and prints the exact
`board-wait.sh` command to run **in the background** so you react to peer updates.

## The non-negotiables (full detail in docs/agent-collaboration-protocol.md)

1. **Stay synced.** ARM `board-wait.sh --self <You>` in the background after every
   turn. "I posted but the peer didn't react" = a missing ARM, not a dead channel.
2. **Push through `scripts/bridge-push.sh <you>`** — never a bare `git push`
   (two agents pushing at once collide).
3. **Respect file lanes** (see the protocol doc). Cross-lane edits get announced
   on the board first.
4. **Review before merge.** Author self-review + tests → the *other* AI reviews
   (GO/REVISE · SHIP/FIX-FIRST) → human approves direction.
5. **Coordinate releases on the board** — check existing tags
   (`gh release list`) before cutting one, so versions don't drift.
6. **Narrate before slow/opaque actions** — both agents. Before a peer call
   (`mcp__codex__codex` / `ask_claude`) or any long step the user can't watch,
   say in one line WHAT you're about to do and ROUGHLY how long, and what a normal
   wait looks like. A predictable heads-up before each opaque step is how the two
   agents earn the user's trust; an un-narrated slow action reads as "stuck."

If you are a brand-new window and unsure of state: run Step 0, read the board's
`## Participants` and the latest `## *Outbox*` entries, then announce yourself.
