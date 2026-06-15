#!/usr/bin/env python3
"""claude-codex-bridge: an MCP (stdio, JSON-RPC) server that lets a Codex agent
talk to a PERSISTENT Claude Code colleague.

Tool exposed: ask_claude(prompt, session_id?, new_session?) -> Claude's reply.

"Online colleague" behavior
---------------------------
- PERSISTENT MEMORY: one Claude session is pinned per working directory. The
  first call creates it (--session-id <uuid>) and stores the id on disk; later
  calls auto --resume it, so the same Claude accumulates memory across calls
  without Codex having to track a session id. Pass session_id to target a
  specific session, or new_session=true to reset this directory's session.
- PROJECT GROUNDING: every call appends a system prompt telling Claude it is the
  project's persistent collaborator and to read ./collaboration.md if present.
- POWERS: read + edit/write files (Read/Grep/Glob/Edit/Write/TodoWrite,
  --permission-mode acceptEdits) by default. NO shell/Bash. NO MCP servers are
  loaded in the spawned Claude (fast startup + cannot recurse back into Codex).

Configuration via environment variables
---------------------------------------
- CLAUDE_BIN              path to the `claude` CLI (default: auto-detected on PATH)
- CLAUDE_CHAT_SESSION_DIR where pinned session ids are stored
                          (default: ~/.claude-codex-bridge/sessions)
- CLAUDE_CHAT_TIMEOUT     per-call timeout in seconds (default: 900)
- CLAUDE_CHAT_ALLOWED_TOOLS  space-separated tool allowlist
                          (default: "Read Grep Glob Edit Write TodoWrite";
                           set to "Read Grep Glob" for a read-only colleague)
- CLAUDE_CHAT_DEBUG       if set to 1, include raw CLI stderr in error replies
"""
import os
import sys
import json
import uuid
import shutil
import hashlib
import subprocess

EMPTY_MCP = '{"mcpServers":{}}'
TIMEOUT_SEC = int(os.environ.get("CLAUDE_CHAT_TIMEOUT", "900"))
SESS_DIR = os.path.expanduser(
    os.environ.get("CLAUDE_CHAT_SESSION_DIR", "~/.claude-codex-bridge/sessions")
)
ALLOWED_TOOLS = os.environ.get(
    "CLAUDE_CHAT_ALLOWED_TOOLS", "Read Grep Glob Edit Write TodoWrite"
).split()
DEBUG = os.environ.get("CLAUDE_CHAT_DEBUG") == "1"

# MCP protocol versions this server can speak. We echo the client's requested
# version when we recognize it, else fall back to a conservative default that
# all current MCP clients understand.
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL = "2024-11-05"


def _resolve_claude():
    """Return an absolute, runnable path to the claude CLI, or None."""
    cand = os.environ.get("CLAUDE_BIN")
    if cand:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        found = shutil.which(cand)
        return found  # None if a bad CLAUDE_BIN was given
    return shutil.which("claude")


CLAUDE = _resolve_claude()

def _find_board(cwd):
    """Locate the collaboration board the way the bridge resolves a project root:
    walk UP from cwd; return <dir>/.collab/collaboration.md if present, else a legacy
    flat <dir>/collaboration.md; stop at a .git boundary (never bind to a PARENT
    project's board) and at the filesystem root. Returns the path, or None.

    (The wrapper is installed standalone and can't import bridge_common, so this
    mirrors find_project_root's boundary in a few lines.)"""
    d = os.path.abspath(cwd)
    while True:
        collab = os.path.join(d, ".collab", "collaboration.md")
        if os.path.isfile(collab):
            return collab
        flat = os.path.join(d, "collaboration.md")
        if os.path.isfile(flat):
            return flat
        if os.path.isdir(os.path.join(d, ".git")):
            return None          # project root reached, no board here
        parent = os.path.dirname(d)
        if parent == d:
            return None          # filesystem root
        d = parent


def grounding(cwd):
    """System prompt for a spawned Claude, pointing at the REAL board path (resolved
    via _find_board) rather than assuming ./collaboration.md in cwd."""
    base = (
        "You are the persistent Claude collaborator for the project in this working "
        "directory, working alongside a Codex agent, reached through the ask_claude "
        "bridge (Codex -> you). You may read project files and make edits "
        "(Read/Grep/Glob/Edit/Write); you do NOT run shell commands. Keep replies "
        "concise and actionable."
    )
    board = _find_board(cwd)
    if board:
        return (base + " A shared collaboration board exists at %s — read it before "
                "answering so you stay aligned with the current task and decisions, and "
                "write durable findings there when that is the agreed channel." % board)
    return base + " No shared collaboration board was found in this project; answer directly."


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(mid, res):
    send({"jsonrpc": "2.0", "id": mid, "result": res})


def error(mid, code, message):
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def tool_text(mid, text, is_error=False):
    result(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})


TOOL = {
    "name": "ask_claude",
    "description": (
        "Talk to the persistent Claude colleague for the current project. It keeps "
        "memory across calls (auto-pinned per working directory) and reads the "
        "project's collaboration.md for context. Use to ask questions, get a "
        "review/second opinion, or hand off a task. Optional: session_id to target "
        "a specific session; new_session=true to start fresh for this project."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "description": "Message/question/task for Claude."},
            "session_id": {"type": "string", "description": "Optional: target a specific Claude session id instead of the project-pinned one."},
            "new_session": {"type": "boolean", "description": "Optional: if true, start a fresh session for this project (resets memory)."},
        },
        "required": ["prompt"],
    },
}


def _sess_path(cwd):
    key = hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:16]
    return os.path.join(SESS_DIR, key + ".session")


def _read_pinned(cwd):
    p = _sess_path(cwd)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return f.read().strip() or None
        except Exception:
            return None
    return None


def _write_pinned(cwd, sid):
    try:
        os.makedirs(SESS_DIR, exist_ok=True)
        with open(_sess_path(cwd), "w") as f:
            f.write(sid)
    except Exception:
        pass


def _base_cmd(prompt, cwd):
    return [
        CLAUDE, "-p", prompt,
        "--output-format", "json",
        "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
        "--append-system-prompt", grounding(cwd),
        "--permission-mode", "acceptEdits",
    ]


def _run(cmd):
    cmd = cmd + ["--allowedTools"] + ALLOWED_TOOLS  # variadic flag must go last
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    out = (p.stdout or "").strip()
    try:
        data = json.loads(out)
        return data.get("result") or out, data.get("session_id", ""), (bool(data.get("is_error")) or p.returncode != 0), True
    except Exception:
        if out:
            detail = out
        elif DEBUG:
            detail = (p.stderr or "").strip() or "no output"
        else:
            detail = "Claude CLI produced no parseable output (set CLAUDE_CHAT_DEBUG=1 for details)."
        return detail, "", (p.returncode != 0), False


def call_claude(prompt, session_id=None, new_session=False):
    cwd = os.getcwd()
    target = session_id or (None if new_session else _read_pinned(cwd))
    fresh_id = None
    cmd = _base_cmd(prompt, cwd)
    if target:
        cmd = cmd + ["--resume", target]
    else:
        fresh_id = str(uuid.uuid4())
        cmd = cmd + ["--session-id", fresh_id]
    text, sid, is_err, parsed = _run(cmd)
    # If a resume failed (stale/missing session), retry once with a fresh session.
    if target and is_err and parsed:
        fresh_id = str(uuid.uuid4())
        text, sid, is_err, parsed = _run(_base_cmd(prompt, cwd) + ["--session-id", fresh_id])
    final_sid = sid or fresh_id or target
    if final_sid and not session_id:
        _write_pinned(cwd, final_sid)
    return text, final_sid, is_err


def client_label(params):
    """Label the calling MCP client (its SURFACE) from the initialize clientInfo:
    'name/version', e.g. 'claude-desktop/1.2' vs 'codex/...'. This is how the bridge
    tells a DESKTOP caller from a CLI caller — a desktop app reaches the bridge only
    over MCP, never via the shell. Returns 'unknown/?' when clientInfo is absent."""
    ci = (params or {}).get("clientInfo")
    if not isinstance(ci, dict):
        ci = {}
    return "%s/%s" % (ci.get("name") or "unknown", ci.get("version") or "?")


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        params = msg.get("params", {}) or {}
        # Record which surface is calling (stderr is safe for MCP stdio; stdout is the
        # protocol channel). Lets a future collab layer route by caller surface.
        sys.stderr.write("[claude_chat] client: %s\n" % client_label(params))
        sys.stderr.flush()
        client_ver = params.get("protocolVersion")
        ver = client_ver if client_ver in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        result(mid, {
            "protocolVersion": ver,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "claude_chat", "version": "2.1"},
        })
    elif method == "notifications/initialized":
        return
    elif method == "ping":
        result(mid, {})
    elif method == "tools/list":
        result(mid, {"tools": [TOOL]})
    elif method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") != "ask_claude":
            error(mid, -32602, "Unknown tool: %s" % params.get("name"))
            return
        if not CLAUDE:
            tool_text(mid, "Error: `claude` CLI not found or not executable. Set CLAUDE_BIN to a valid path or add claude to PATH.", True)
            return
        args = params.get("arguments", {}) or {}
        prompt = args.get("prompt", "")
        if not prompt:
            tool_text(mid, "Error: prompt is required.", True)
            return
        try:
            text, sid, is_err = call_claude(prompt, args.get("session_id") or None, bool(args.get("new_session")))
            body = text if not sid else "%s\n\n[session_id: %s]" % (text, sid)
            tool_text(mid, body, is_err)
        except subprocess.TimeoutExpired:
            tool_text(mid, "Claude call timed out after %ds." % TIMEOUT_SEC, True)
        except Exception as e:
            tool_text(mid, "Wrapper error: %s" % e, True)
    elif mid is not None:
        # Unknown request method: respond per JSON-RPC instead of hanging.
        error(mid, -32601, "Method not found: %s" % method)
    # Unknown notifications (no id) are ignored.


def main():
    # IMPORTANT: read with readline(), NOT `for line in sys.stdin`. The latter
    # block-buffers on a pipe (waits ~8 KB before yielding a line), so a lone
    # `initialize` message never reaches the handler and the MCP client hangs.
    while True:
        line = sys.stdin.readline()
        if line == "":  # EOF
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            # Malformed line; we can't recover an id, so skip rather than emit a
            # spec parse-error with a null id (which most clients ignore anyway).
            continue
        handle(msg)


if __name__ == "__main__":
    main()
