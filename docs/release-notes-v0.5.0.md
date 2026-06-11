# v0.5.0 — Collaboration framework: multi-agent + reactive interactive agents

This is the milestone where `claude-codex-bridge` becomes a **collaboration
framework**, not just a transport bridge. Everything below shipped since v0.4.3
(which was docs-only) and is built with the project's own Claude+Codex review
workflow.

## Highlights

- **Multi-agent scalability (`--queue-mode`).** N agents per AI collaborate via a
  work queue (`collaboration_queue.json`) instead of a single `next_actor` token:
  claim-under-lock, `claim_epoch` fencing (rejects a zombie/late commit), per-agent
  outboxes, idempotent enqueue, dependency gating, claim-time budget reservation.
  Validated on the 3-party scenario: 2 Codex executors in parallel + 1 Claude
  reviewer. Opt-in and isolated — the 2-actor path is untouched.
- **Reactive interactive agents (`board-wait.sh`).** A headless watcher already
  auto-reacts to the board, but an interactive chat agent is request/response and
  can't be woken by an external board write. `board-wait.sh` closes that gap: an
  interactive agent ARMs it in the background and wakes exactly when the peer
  updates the board. "I posted but the peer didn't react" is now a missing ARM,
  not a broken channel (see the protocol doc).
- **`bridge_common.py` refactor.** Shared primitives (atomic I/O, the global lock,
  logging, board helpers, role logic, model runners) are now one module; the
  2-actor harness dropped from ~740 to ~413 lines. Behavior-preserving.
- **Resilience/role layer (complete).** Pluggable roles (planner/executor/reviewer),
  mechanical self-repair (bounded retry), graceful single-turn handoff (coverer),
  propose-only self-improvement (improver), and an observe-only stale-peer
  heartbeat — all gated, bounded, and harness-enforced.
- **Token control.** Bounded-board archive rotation (`compact-collaboration.sh`)
  keeps `collaboration.md` small on large projects, losslessly.
- **Push coordination + lane discipline.** `bridge-push.sh` serializes pushes
  between two agents; `docs/agent-collaboration-protocol.md` documents file lanes.
- **Multi-perspective review standard.** Author self-review + tests → peer-AI
  review → fresh-subagent code-quality pass → human approves. Formalized in the
  protocol doc.
- **Framework positioning.** README, resource-aware routing (full
  subscription/billing/context/permission matrix, incl. credit-card auto-reload),
  and a real autonomous review-loop example.

## Safety posture (unchanged, reaffirmed)

- Caps are explicit; write permission is controlled by install mode and watcher
  flags; roles can only narrow the `--allow-write` ceiling.
- Codex cost is not parseable, so the Codex side is governed by `max_turns`, not
  `max_cost`. **For API billing with credit-card auto-reload, `max_cost_usd` /
  `max_turns` are the only reliable brakes — never run an autonomous loop without
  explicit caps.**
- Multi-agent (`--queue-mode`) is an opt-in MVP; sharding, multi-board, lease
  renewal, and hard-block auto-coverage are deliberately deferred.

## Honest boundaries

This runs over CLI agents: there is no in-window message injection and no true
duplex streaming. "Real-time" means seconds-latency, event/poll-driven turns. The
durable shared board is the channel between already-running windows; MCP is the
transport for spawning/poking.
