# Cookbook Examples

These examples are copy-pasteable collaboration patterns for the installed MCP
bridge. Replace bracketed placeholders with your project-specific task.

## Review -> Implement -> Re-review

Use when Codex has a draft implementation and you want Claude to review before
more edits.

Tool call prompt:

```text
call mcp__claude_chat__ask_claude with prompt:
"Read collaboration.md if present. Review the current diff for [feature or bug].
Focus on blocker/high issues, missing tests, and behavioral regressions. Do not
edit files. Write a concise verdict and actionable findings."
```

Expected handoff:

```text
Claude returns GO / NO-GO / REVISE plus findings. If changes are needed, Codex
implements only the actionable findings, runs tests, then calls Claude again:

call mcp__claude_chat__ask_claude with prompt:
"Re-review the latest diff for [feature or bug]. Confirm whether the previous
findings are fixed. Report only blocker/high issues."
```

## Run Tests And Summarize Failures

Use when Claude is steering and wants Codex to run local checks.

Tool call prompt:

```text
call mcp__codex__codex with prompt:
"Run [test command] in [repo path]. Do not edit files. Summarize failing tests,
likely causes, and the smallest next fix. Include the exact failing test names
and key error lines."
```

Expected handoff:

```text
Codex reports command status, failing tests, and a recommended next step. Claude
uses that result to decide whether to ask Codex for a bounded fix or to revise
the plan.
```

## Docs Update After Implementation

Use when code is already merged or locally complete and the docs need to catch
up without reopening product decisions.

Tool call prompt:

```text
call mcp__codex__codex with prompt:
"Update docs for [implemented change]. Scope: README and relevant docs/examples
only. Preserve existing terminology. Do not change runtime code. Add or update a
small docs test if the repo already tests documentation links."
```

Expected handoff:

```text
Codex edits the docs, runs available doc/unit checks, and summarizes changed
files. Claude can then review wording, scope claims, and whether the docs imply
unsupported behavior.
```

## Shared Board Handoff

Use when both agents need durable context instead of relying on chat history.

Tool call prompt:

```text
call mcp__claude_chat__ask_claude with prompt:
"Read collaboration_signal.json first. If update_id changed, read
collaboration.md. Add your findings to the Claude Outbox only, then update
collaboration_signal.json with a one-line summary. Do not edit implementation
files."
```

Expected handoff:

```text
Claude writes its outbox entry and bumps collaboration_signal.json. Codex reads
the signal, reloads collaboration.md only if update_id changed, then either acts
on the request or writes a Codex Outbox response and bumps the signal again.
```

## Claude Max + Codex Pro Preset

Use when Claude has the deeper reasoning budget and Codex should do most bounded
execution work.

Tool call prompt:

```text
scripts/apply-role-preset.py --project . --preset max-claude-pro-codex
scripts/bridge-status.py --project .

call mcp__claude_chat__ask_claude with prompt:
"We are using the max-claude-pro-codex preset. Stay in the Claude lane:
architecture, ambiguity resolution, test strategy, strict review, and final QA.
Hand bounded implementation and test iteration back to Codex."
```

Expected handoff:

```text
collaboration_state.json records Claude as reviewer and Codex as executor, with
resource_profiles visible in scripts/bridge-status.py. Claude escalates only
judgment-heavy work; Codex handles implementation, search, tests, and mechanical
docs updates. Pause the autonomous loop before applying presets, or pass
--force only when intentionally overriding the guard.
```
