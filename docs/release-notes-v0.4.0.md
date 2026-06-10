# v0.4.0

Collaboration framework promotion release.

## Highlights

- README now positions the project as a Claude Code + Codex collaboration
  framework, not only a transport bridge.
- Resource-aware routing covers common Claude/Codex subscription, quota, billing,
  context-window, and permission pairings.
- Autonomous review-loop example shows a bounded reviewer/executor loop
  converging to `status=done`.
- Existing safety posture remains: caps are explicit, write permission is
  controlled by install mode and watcher flags, and Codex cost is governed by
  turn caps because parseable cost is not currently available.

## Promotion Notes

Use this release when presenting the project as a reusable multi-agent workflow:

- MCP is the phone line.
- `collaboration.md` and `collaboration_signal.json` are the durable shared
  board.
- Watchers and `_auto_turn.py` make the loop event-driven and bounded.
- Resource profiles keep scarce reasoning, context, quota, and write authority
  visible instead of hidden in prompts.

## Safety

For API billing with credit-card auto-reload, local `max_cost_usd` and
`max_turns` are the only reliable brakes. Do not run autonomous loops without
explicit caps.
