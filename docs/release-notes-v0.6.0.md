# v0.6.0 — Membership + project-rooted layout: agents actually find each other

The release where two agents in the same project **reliably discover, join, and
stay in sync** — the gap that previously made collaboration fail in practice.
Everything below shipped since v0.5.0 and was hardened through the project's own
Claude+Codex review loop (and surfaced real bugs while doing it).

## Highlights

- **Project-rooted `.collab/` layout + auto-location.** Coordination files now live
  in one tidy `<project-root>/.collab/` and every script auto-locates the project
  root from any cwd depth — `--project` is optional (a shared `bridge-paths.sh` /
  `collab_paths()` resolver backs both the Python and shell tools). This fixes the
  failure that bit us live: an agent whose cwd was elsewhere couldn't find the board
  at all. The root finder respects nested git/submodule boundaries (never binds to a
  parent project's `.collab/`).
- **Membership protocol — join, presence, departure.** `AGENTS.md` / `CLAUDE.md` at
  the project root tell a fresh window to JOIN before working;
  `join-collaboration.sh` registers an agent (one protocol per project). While an
  agent keeps `board-wait.sh` armed, a presence heartbeat refreshes its `last_seen`
  (and `board-wait` — shipped in v0.5.0 — now also drives this heartbeat and the
  departure scan); when a peer goes stale, the first to notice **broadcasts the
  departure** to the board. When agents follow the root instructions and stay armed,
  openings and stale departures become visible on the board instead of silent.
- **Bootstrap paths resolve in any target project.** When `init` is run into another
  project, `AGENTS.md`/`CLAUDE.md` and the printed join/watch/ARM commands now point
  at the absolute bridge scripts path, so they actually run where the project lives.
- **Dashboards/presets follow the new layout.** `bridge-status.py` and
  `apply-role-preset.py` read through the resolved `.collab/` paths (no more stale
  legacy-flat readings); back-compat fallback preserved for existing flat setups.

## Fixes surfaced by real collaboration

- Presence false-departure: the heartbeat threshold (180s) false-flagged a
  present-but-bursty interactive agent as departed; raised to 1800s (30 min), and a
  departed flag self-heals on the agent's next write.

## Upgrade notes

- New projects: `init-collaboration.sh` creates `<root>/.collab/` + `AGENTS.md` /
  `CLAUDE.md` at the root. Any agent in the project (any cwd depth) auto-finds it.
- Existing flat setups keep working (legacy fallback) until you migrate to
  `.collab/`.
- Heartbeat/presence requires an agent to keep `board-wait.sh` armed in the
  background; bursty agents that aren't armed appear quiet (recoverable on next
  write), not departed, within the 30-min window.

## Honest boundaries (unchanged)

CLI agents, no in-window message injection, no true duplex streaming. The durable
shared board is the channel between already-running windows; MCP is the transport
for spawning/poking. Codex cost is governed by `max_turns` (not parseable as $).
