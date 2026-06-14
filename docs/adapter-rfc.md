# Adapter RFC and Capability Matrix

Status: RFC

Issue: #10, "Defer multi-CLI support behind an adapter RFC and capability matrix"

## Decision

Do not implement adapters from this RFC. Multi-CLI support remains a future
direction. The current product pitch stays Claude Code + Codex first because
that workflow is already installed, documented, tested, and exercised by this
repository.

The only recommended next engineering step is Adapter contract first: define a
small, testable boundary that any future CLI adapter must satisfy before the
bridge starts carrying implementation branches for Gemini CLI, Aider, OpenCode,
Cursor, or another tool.

No CLI-specific implementation should start until a candidate can pass the
contract probes in this document on macOS and Linux.

## Why Defer Implementation

Adding more CLIs sounds like a simple routing problem, but every adapter becomes
part transport wrapper, part permission boundary, part session manager, and part
failure classifier. The cost is not just "can it run a prompt?" The bridge needs
predictable behavior across:

- headless prompt support
- session persistence
- machine-readable output
- permission controls
- cost reporting
- MCP compatibility
- install complexity
- project context files
- write safety and dry-run behavior
- timeout and error semantics

Claude Code and Codex should remain the sharp primary workflow until this matrix
has been validated against real releases.

## Proposed Adapter Contract

A future adapter should expose one deterministic operation:

```text
run_agent_turn(project, prompt, session_key, mode, allowed_tools, timeout)
  -> AgentTurnResult
```

`AgentTurnResult` must include:

- `status`: `ok`, `rejected`, `rate_limited`, `timed_out`, or `failed`
- `text`: the assistant-visible response
- `structured`: optional parsed JSON or event stream summary
- `session_ref`: a resumable session identifier, if the CLI supports one
- `cost`: optional model cost or token usage
- `changed_files`: optional list of project files the CLI reports changing
- `raw_command`: redacted argv for debugging

The adapter boundary must not grant write authority by itself. Write authority
still comes from the caller's configured mode and the bridge's existing
coordination rules.

## Capability Matrix

Legend: Strong means the public docs show a plausible supported path today.
Promising means the public docs suggest the capability exists, but the adapter
probe still must verify exact behavior. Unknown means the adapter probe must
verify the behavior before implementation. Weak means the model is likely useful
to humans but risky as an unattended bridge adapter.

| Candidate | headless prompt | session persistence | machine-readable output | permission controls | cost reporting | MCP compatibility | install complexity | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Code | Strong | Strong | Strong enough for current bridge | Strong enough for tool allowlists | Partial | Strong enough through MCP server registration | Medium | Already supported; baseline for contract behavior. |
| Codex | Strong | Strong enough for MCP server sessions | Strong enough through MCP tool result | Strong enough through Codex sandbox and approvals | Unknown/partial | Strong | Medium | Already supported; keep as the executor baseline. |
| Gemini CLI | Strong | Promising | Strong | Promising | Unknown | Strong | Medium | Official docs describe non-interactive `-p`, JSON and stream JSON output, checkpointing, and MCP support. Safest new candidate if local probes confirm write controls. |
| Aider | Promising | Promising | Unknown | Weak/unknown | Unknown | Unknown | Medium | Mature terminal pair-programming flow and broad model support, but the bridge must verify unattended output, permission boundaries, and auto-commit behavior before use. |
| OpenCode | Unknown/promising | Unknown | Unknown | Unknown | Unknown | Unknown | Medium | Docs show a terminal coding agent with provider configuration and project initialization. Need specific headless, structured-output, permission, and MCP probes before adapter work. |
| Cursor | Unknown | Unknown | Unknown | Unknown | Unknown | Unknown | High | Useful ecosystem, but current public CLI docs need verification before treating it as a safe headless adapter target. |

## Safest First Adapter Candidate

The safest first adapter candidate is Gemini CLI, but only after an adapter-probe
spike confirms all required contract fields.

Reasoning:

- It has documented non-interactive prompt execution.
- It has documented JSON and stream JSON output modes.
- It has documented MCP support.
- It has documented conversation checkpointing.
- It is terminal-first and installable by common package managers.

The main unresolved risk is permission control. A bridge adapter must prove it
can run in a read-only/review mode and a bounded write mode without relying only
on prompt instructions.

If that risk fails, the next safest path is not to pick Aider, OpenCode, or
Cursor immediately. It is to harden the adapter contract with a "read-only
reviewer only" mode and rerun the probes.

## Probe Plan Before Implementation

For each candidate, create a temporary repository and run the same probes:

1. Headless response: ask for a repository summary and capture stdout/stderr.
2. Structured output: request JSON and verify parseable machine output.
3. Session resume: ask a follow-up question that depends on prior context.
4. Read-only guard: ask for a change while write permission is disabled.
5. Bounded write: allow one file edit and verify the changed file list.
6. Timeout: run a prompt that exceeds a short timeout and classify the result.
7. Rate limit or auth failure: run with missing credentials and classify the
   failure without hanging.
8. MCP/tooling: verify whether MCP servers can be registered or called without
   recursive agent loops.

The result should be a small probe report, not production bridge code.

## Non-Goals

- No adapter implementation in this RFC.
- No automatic routing among more than Claude Code and Codex.
- No claim that Windows support improves through any adapter.
- No weakening of the current install, approval, sandbox, or board-handshake
  safety posture.

## README Positioning Rule

Keep Claude Code + Codex as the primary pitch. The README may link this RFC as a
future-adapter planning document, but it should not imply that Gemini CLI, Aider,
OpenCode, Cursor, or any other CLI is supported by this bridge today.

## Sources Checked

- Gemini CLI official repository/docs, checked 2026-06-14:
  <https://github.com/google-gemini/gemini-cli>
- Aider usage docs, checked 2026-06-14:
  <https://aider.chat/docs/usage.html>
- OpenCode docs, checked 2026-06-14:
  <https://opencode.ai/docs>
- Cursor docs landing page, checked 2026-06-14:
  <https://cursor.com/docs>
