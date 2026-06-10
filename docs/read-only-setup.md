# Read-Only Setup

Use this setup when you want to evaluate the bridge with the smallest useful
Claude-side permission set. Codex can still ask Claude for reviews and answers,
but the spawned Claude colleague cannot edit or write files.

## Install In Read-Only Mode

From the repository root:

```bash
BRIDGE_READONLY=1 ./install.sh
```

That writes the `claude_chat` MCP server with:

```toml
CLAUDE_CHAT_ALLOWED_TOOLS = "Read Grep Glob"
```

The read-only boundary is enforced by Claude CLI's `--allowedTools` setting.

The default install uses:

```toml
CLAUDE_CHAT_ALLOWED_TOOLS = "Read Grep Glob Edit Write TodoWrite"
```

There is no `Bash` access in either mode unless you explicitly add it yourself.

## Convert An Existing Install To Read-Only

If `[mcp_servers.claude_chat]` already exists, the installer leaves it alone.
Edit `~/.codex/config.toml` and set:

```bash
CLAUDE_CHAT_ALLOWED_TOOLS="Read Grep Glob"
```

In TOML, that appears as:

```toml
[mcp_servers.claude_chat.env]
CLAUDE_CHAT_ALLOWED_TOOLS = "Read Grep Glob"
```

Restart Codex after changing the config.

## Confirm The Effective Config

Use this read-only check to print only the `claude_chat` block with common secret
keys redacted:

```bash
python3 - <<'PY'
from pathlib import Path

path = Path.home() / ".codex" / "config.toml"
text = path.read_text()
lines = text.splitlines()
inside = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        inside = stripped in {
            "[mcp_servers.claude_chat]",
            "[mcp_servers.claude_chat.env]",
        }
    if not inside:
        continue
    if any(key in line.upper() for key in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
        name = line.split("=", 1)[0].rstrip()
        print(f"{name} = \"<redacted>\"")
    else:
        print(line)
PY
```

Expected read-only output should include:

```toml
[mcp_servers.claude_chat.env]
CLAUDE_BIN = "/path/to/claude"
CLAUDE_CHAT_ALLOWED_TOOLS = "Read Grep Glob"
```

If you see `Edit`, `Write`, or `TodoWrite`, the Claude colleague is not
read-only.

## What Read-Only Does Not Change

- It does not change Claude Code -> Codex registration.
- It does not change Codex's own local permissions.
- It does not change autonomous watcher write authority; that still comes from
  `scripts/watch-collaboration.sh --allow-write`.
- It does not grant any extra role or preset permissions.
