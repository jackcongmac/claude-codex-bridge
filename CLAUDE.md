# For Claude working in this repo (mirror of AGENTS.md, which Codex reads)

**This is a multi-agent collaboration project. Before doing project work, JOIN the
collaboration protocol — do not work in isolation.**

## Step 0 — Join (run this first, every fresh session)

```bash
scripts/join-collaboration.sh --self Claude --role <peer|planner|executor|reviewer>
```

It prints the live rules, registers you in the participants list, shows the
current board state, and prints the exact `board-wait.sh` command to run **in the
background** so you react to peer updates the moment they happen.

## The non-negotiables (full detail in docs/agent-collaboration-protocol.md)

1. **Stay synced.** ARM `board-wait.sh --self Claude` in the background after every
   turn. "I posted but the peer didn't react" = a missing ARM, not a dead channel.
2. **Push through `scripts/bridge-push.sh claude`** — never a bare `git push`.
3. **Respect file lanes** (protocol doc). Cross-lane edits get announced on the
   board first.
4. **Review before merge.** Author self-review + tests → the *other* AI reviews
   (GO/REVISE · SHIP/FIX-FIRST) → human approves direction.
5. **Coordinate releases on the board** — check `gh release list` before cutting a
   version so numbers don't drift (this exact drift already happened once).
6. **Narrate before slow/opaque actions** — both agents. Before a peer call
   (`mcp__codex__codex` / `ask_claude`) or any long step the user can't watch,
   say in one line WHAT you're about to do and ROUGHLY how long, and what a normal
   wait looks like. A predictable heads-up before each opaque step is how the two
   agents earn the user's trust; an un-narrated slow action reads as "stuck."
7. **Handshake before you hand off.** Don't dump a task into the board or fire a
   blocking peer call until the channel is confirmed live. Run
   `scripts/bridge-handshake.sh --self Claude --peer <Them>` first: it fast-fails
   with a fix if the peer isn't ARMed/joined, and prints a GO confirmation when both
   sides are listening. Silence from the peer = a failed handshake, not a dead
   channel — never leave the user staring at a hung "Calling…".

If you are a brand-new window and unsure of state: run Step 0, read the board's
`## Participants` and latest `## *Outbox*` entries, then announce yourself.
