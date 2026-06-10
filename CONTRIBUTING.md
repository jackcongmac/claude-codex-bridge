# Contributing

Thanks for your interest in improving **claude-codex-bridge**. It's a tiny,
dependency-free project, so contributing is lightweight.

## Project layout

| File | What it is |
| --- | --- |
| `claude_chat_mcp.py` | The MCP stdio server (Codex → Claude). Pure Python stdlib. |
| `install.sh` | Idempotent installer that wires up both directions. |
| `skill/SKILL.md` | Claude Code skill describing install/use/troubleshooting. |
| `README.md` | Architecture, install, configuration, security. |

## Development setup

You need the `claude` and `codex` CLIs installed and logged in, plus `python3`.
There is nothing to build and nothing to `pip install`.

## Testing your change

The server speaks newline-delimited JSON-RPC over stdio. You can drive it directly
without Codex:

```bash
python3 - <<'PY'
import subprocess, json, select
p = subprocess.Popen(["python3", "claude_chat_mcp.py"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def send(o): p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
def recv(t=10):
    r, _, _ = select.select([p.stdout], [], [], t)
    return p.stdout.readline() if r else None
send({"jsonrpc":"2.0","id":1,"method":"initialize",
      "params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}})
print("initialize:", recv())
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
print("tools/list:", recv())
p.kill()
PY
```

Send messages **one at a time and wait for each reply** — that's how real MCP
clients behave, and it catches the kind of bug noted below.

## The one rule that will save you hours

An MCP stdio server **must read stdin with `readline()`**, never
`for line in sys.stdin`. The iterator block-buffers on a pipe (it waits to fill
~8 KB before yielding a line), so a lone `initialize` never reaches your handler
and the client hangs forever. It *looks* fine when you feed several messages at
once in a test, then hangs against a real client. Keep the `while True:
sys.stdin.readline()` loop.

## Pull requests

- Keep it dependency-free (stdlib only) and cross-platform where reasonable.
- If you touch the protocol layer, test `initialize` (with version negotiation),
  `tools/list`, `tools/call`, `ping`, and an unknown method.
- If you change defaults that affect what the Claude colleague can do
  (`CLAUDE_CHAT_ALLOWED_TOOLS`, permission mode), call it out clearly — this is a
  security-sensitive surface.
- Run the snippet above and confirm no hangs before opening the PR.

## License

By contributing you agree your contributions are licensed under the
[MIT License](LICENSE).
