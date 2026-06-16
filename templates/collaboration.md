# Agent Collaboration Board

> Shared coordination file for two agents (e.g. **Claude Code** and **Codex**) and
> a human working on the same project. The MCP bridge is the "phone line"; this
> file is the durable shared memory both agents read before acting and write to
> after meaningful work. Pair it with `collaboration_signal.json` so agents can
> cheaply detect changes without re-reading this whole file.

Last updated by: <agent> — <YYYY-MM-DD HH:MM TZ>

## Roles

| Role | Owner | Authority |
| --- | --- | --- |
| Final decision maker | <human> | Direction, taste, approvals |
| Executor / integrator | <agent A, e.g. Codex> | Builds, edits, runs, integrates |
| Reviewer / QA | <agent B, e.g. Claude> | Independent review, verification |

> Roles are a convention, not enforced by code. Adapt to your project. A common
> split: one agent executes, the other independently reviews; the human approves.

## Resource Strategy

Use agent strengths and subscription constraints intentionally; do not alternate
turns just for symmetry.

Default `max-claude-pro-codex` split:

- **Claude Max**: high-leverage reasoning, architecture, ambiguity resolution,
  strict review, test strategy, and final QA.
- **Codex Pro**: bounded implementation, search, small fixes, test iteration,
  and mechanical documentation updates.
- **Human**: final product judgment, budget/risk decisions, and scope changes.

Routing rule of thumb: use Codex first for clear execution; escalate to Claude
when the next decision needs broad context, adversarial review, or architectural
judgment; ask the human when the decision changes scope, risk, cost, or taste.

## Operating Rules

1. Read this board (or at least `collaboration_signal.json`) before acting.
2. Sections marked `OWNED BY <agent>` are only edited by that agent.
3. Either agent may append to `Decision Log`, with owner + timestamp + reason.
4. Do not rewrite another agent's notes — add a correction beneath them instead.
5. Update `File Locks` before editing shared/generated files.
6. Anything needing the human's decision goes to `Open Questions`, not a guess.
7. Post via `bridge-post.sh --self <You> --message "…"` — it appends to your Outbox
   here AND bumps `collaboration_signal.json` in one locked step (don't hand-edit the
   board and bump the signal separately — that can lose updates).

## Low-Token Signal Protocol

To avoid re-reading this whole file on every poll, agents watch
`collaboration_signal.json`:

1. Post with `bridge-post.sh` — it appends here AND updates the signal file in one
   locked step (don't hand-edit + bump separately).
2. Pollers read only `collaboration_signal.json` first.
3. If `update_id` is unchanged, do nothing.
4. If `update_id` changed, read only the section named by `changed_section`.

## Handoff Format

When one agent needs the other to do something, write it under your Outbox:

```text
ACTION_REQUEST:
- Priority: Critical / Important / Minor
- Routing reason: execution / review / architecture / ambiguity / human decision
- Where: <file / segment / time>
- Problem:
- Requested action:
- Files likely involved:
- Needs human decision: yes/no
```

## Current Project State

Project: <name>

- <key fact / current focus>
- <key paths>
- <known status>

## File Locks

OWNED BY <executor agent>

```text
Current lock: None.
```

Lock format:

```text
LOCKED_BY: <agent>
TIME: <YYYY-MM-DD HH:MM TZ>
FILES:
- /absolute/path
REASON:
EXPECTED_RELEASE:
```

## Codex Outbox

OWNED BY Codex

<!-- Codex appends status, findings, and action requests here. -->

## Claude Outbox

OWNED BY Claude

<!-- Claude appends review findings and action requests here. -->

## Open Questions

Shared — items needing the human's decision.

- <question> (raised by <agent>, <date>)

## Decision Log

Shared, append-only.

### <YYYY-MM-DD HH:MM TZ> — <agent/human>

<decision + reason>
