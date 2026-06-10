# Security

`claude-codex-bridge` intentionally lets one local coding agent call another.
That is powerful, but it means the bridge should be installed with an explicit
trust model.

## Default behavior

By default, the Claude colleague can read and edit files in the working
directory where Codex calls it. It runs with:

```text
Read Grep Glob Edit Write TodoWrite
```

It does not get `Bash` by default, and the spawned Claude process is started
without nested MCP servers to avoid recursive tool calls.

## Read-only mode

For the safest setup, install read-only:

```bash
BRIDGE_READONLY=1 ./install.sh
```

Or set the Codex MCP server environment variable:

```bash
CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"
```

## Write mode

Write mode is useful when you want Codex to ask Claude to make targeted edits.
Only use it in repositories where both agents are allowed to modify files.

Before enabling write mode in sensitive projects:

- Keep secrets out of the working tree.
- Review `~/.codex/config.toml` before sharing logs or screenshots.
- Prefer project-specific working directories.
- Use Git to inspect every file changed by either agent.

## Shell access

Do not add `Bash` to `CLAUDE_CHAT_ALLOWED_TOOLS` unless you deliberately want
Codex to be able to drive shell commands through Claude. That expands the trust
boundary from file edits to arbitrary local command execution.

## Reporting security issues

Please open a GitHub issue with a minimal reproduction if the report can be
shared publicly. If the issue involves credentials, private project data, or a
local privilege boundary, avoid posting secrets and describe the impact at a high
level first.
