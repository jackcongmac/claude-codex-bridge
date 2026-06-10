# Review Loop Example

This is the smallest workflow that shows why the bridge is useful: Codex asks
Claude for a review, Codex makes the change, then Claude checks the result with
the same project-aware session.

## 1. Initialize the coordination files

From your project root:

```bash
/path/to/claude-codex-bridge/scripts/init-collaboration.sh .
```

This creates:

- `collaboration.md` for roles, outboxes, file locks, decisions, and open
  questions.
- `collaboration_signal.json` for cheap polling.

## 2. From Codex, ask Claude to review

Call `mcp__claude_chat__ask_claude`:

```text
Read collaboration.md and review the current implementation. Focus on correctness,
security, missing tests, and anything that would block merging. Write your findings
to your Claude outbox.
```

Claude reads the project files, keeps the session pinned to this directory, and
returns a review.

## 3. Make the change in Codex

Ask Codex to implement the fix from Claude's review. After the change, update
`collaboration.md` with:

- what changed
- which files changed
- what checks ran
- what still needs review

Bump `collaboration_signal.json` so the other side knows the board changed.

## 4. Ask Claude to re-review the same thread

Call `mcp__claude_chat__ask_claude` again:

```text
Re-read collaboration.md and re-review the changes Codex just made. Confirm
whether your previous findings are resolved, and list any remaining blockers.
```

The Claude side resumes the same project session automatically, so it remembers
the previous review and can compare the new state against it.

## 5. Optional: reverse the direction

From Claude Code, call Codex:

```text
Use mcp__codex__codex to run the relevant tests and summarize failures.
Continue the same Codex thread for follow-up commands.
```

This gives you a practical loop:

```text
Claude reviews -> Codex executes -> Claude re-reviews -> Codex verifies
```

The bridge is just the transport. The durable teamwork comes from the shared
board plus each agent's persistent project context.
