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
  --permission-mode acceptEdits). NO shell/Bash. NO MCP servers loaded in the
  spawned Claude (fast startup + cannot recurse back into Codex).

Configuration via environment variables
---------------------------------------
- CLAUDE_BIN              path to the `claude` CLI (default: auto-detected on PATH)
- CLAUDE_CHAT_SESSION_DIR where pinned session ids are stored
                          (default: ~/.claude-codex-bridge/sessions)
- CLAUDE_CHAT_TIMEOUT     per-call timeout in seconds (default: 900)
- CLAUDE_CHAT_ALLOWED_TOOLS  space-separated tool allowlist
                          (default: "Read Grep Glob Edit Write TodoWrite")
"""
import os
import sys
import json
import uuid
import shutil
import hashlib
import subprocess

CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
EMPTY_MCP = '{"mcpServers":{}}'
TIMEOUT_SEC = int(os.environ.get("CLAUDE_CHAT_TIMEOUT", "900"))
SESS_DIR = os.path.expanduser(
    os.environ.get("CLAUDE_CHAT_SESSION_DIR", "~/.claude-codex-bridge/sessions")
)
ALLOWED_TOOLS = os.environ.get(
    "CLAUDE_CHAT_ALLOWED_TOOLS", "Read Grep Glob Edit Write TodoWrite"
).split()

GROUNDING = (
    "You are the persistent Claude collaborator for the project in this working "
    "directory, working alongside a Codex agent. You are being reached through the "
    "ask_claude bridge (Codex -> you). If a shared board file 'collaboration.md' "
    "exists in the working directory, read it before answering so you stay aligned "
    "with the current task and decisions. You may read project files and make edits "
    "(Read/Grep/Glob/Edit/Write); you do NOT run shell commands. Keep replies "
    "concise and actionable, and write durable findings to collaboration.md when "
    "that is the agreed channel."
)


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(mid, res):
    send({"jsonrpc": "2.0", "id": mid, "result": res})


def error(mid, code, message):
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


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
            return open(p).read().strip() or None
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


def _base_cmd(prompt):
    return [
        CLAUDE, "-p", prompt,
        "--output-format", "json",
        "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
        "--append-system-prompt", GROUNDING,
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
        return (out or (p.stderr or "").strip() or "no output"), "", (p.returncode != 0), False


def call_claude(prompt, session_id=None, new_session=False):
    cwd = os.getcwd()
    target = session_id or (None if new_session else _read_pinned(cwd))
    fresh_id = None
    cmd = _base_cmd(prompt)
    if target:
        cmd = cmd + ["--resume", target]
    else:
        fresh_id = str(uuid.uuid4())
        cmd = cmd + ["--session-id", fresh_id]
    text, sid, is_err, parsed = _run(cmd)
    # If a resume failed (stale/missing session), retry once with a fresh session.
    if target and is_err and parsed:
        fresh_id = str(uuid.uuid4())
        text, sid, is_err, parsed = _run(_base_cmd(prompt) + ["--session-id", fresh_id])
    final_sid = sid or fresh_id or target
    if final_sid and not session_id:
        _write_pinned(cwd, final_sid)
    return text, final_sid, is_err


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        result(mid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "claude_chat", "version": "2.0"},
        })
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        result(mid, {"tools": [TOOL]})
    elif method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") != "ask_claude":
            error(mid, -32602, "Unknown tool: %s" % params.get("name"))
            return
        if not CLAUDE:
            result(mid, {"content": [{"type": "text", "text": "Error: `claude` CLI not found. Set CLAUDE_BIN or add claude to PATH."}], "isError": True})
            return
        args = params.get("arguments", {}) or {}
        prompt = args.get("prompt", "")
        if not prompt:
            result(mid, {"content": [{"type": "text", "text": "Error: prompt is required."}], "isError": True})
            return
        try:
            text, sid, is_err = call_claude(prompt, args.get("session_id") or None, bool(args.get("new_session")))
            body = text if not sid else "%s\n\n[session_id: %s]" % (text, sid)
            result(mid, {"content": [{"type": "text", "text": body}], "isError": is_err})
        except subprocess.TimeoutExpired:
            result(mid, {"content": [{"type": "text", "text": "Claude call timed out after %ds." % TIMEOUT_SEC}], "isError": True})
        except Exception as e:
            result(mid, {"content": [{"type": "text", "text": "Wrapper error: %s" % e}], "isError": True})
    elif mid is not None:
        error(mid, -32601, "Unknown method: %s" % method)


def main():
    # IMPORTANT: use readline(), NOT `for line in sys.stdin`. The latter block-
    # buffers on a pipe (waits ~8KB before yielding a line), so a lone
    # `initialize` message never reaches us and the MCP client hangs forever.
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
            continue
        handle(msg)


if __name__ == "__main__":
    main()
