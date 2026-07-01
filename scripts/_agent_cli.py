"""_agent_cli.py - actor->capability registry: how each actor is spawned for chat / implement /
review. Keeps codex/claude invocation OUT of the call sites so roles can rotate (roles-nominal)
and so the chat path can use a warm resumed session (P0.5 latency)."""
import json
import os
import shutil
import subprocess
import tempfile

KNOWN = ("Claude", "Codex")
NAMESPACE_TIMEOUT_ENV = "BRIDGE_CHAT_TURN_TIMEOUT"


def codex_bin():
    return os.environ.get("CODEX_BIN") or "codex"


def claude_bin():
    return os.environ.get("CLAUDE_BIN") or "claude"


def chat_argv(actor, prompt, project, image_path=None, session_id=None):
    if actor == "Codex":
        if session_id:
            argv = [codex_bin(), "exec", "resume", session_id]
        else:
            argv = [codex_bin(), "exec", "--json"]
        if image_path:
            argv += ["-i", image_path]
        argv += ["-C", project, "--skip-git-repo-check", "--ignore-user-config",
                 "-s", "read-only", prompt]
        return argv
    # Claude (headless, read-capable for images)
    p = prompt
    if image_path:
        p += ("\n\n[The user attached an image at: %s - use your Read tool to view it "
              "before replying.]" % image_path)
    return [claude_bin(), "-p", p, "--output-format", "json", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "default",
            "--allowedTools", "Read", "Grep", "Glob"]


def implement_argv(actor, prompt, project, image_path=None):
    if actor == "Codex":
        argv = [codex_bin(), "exec", "-s", "workspace-write"]
        if image_path:
            argv += ["-i", image_path]
        argv += [prompt]
        return argv
    # Claude headless implement (write-capable)
    return [claude_bin(), "-p", prompt, "--permission-mode", "acceptEdits",
            "--allowedTools", "Read", "Edit", "Write", "Bash"]


def review_argv(actor, prompt, project):
    if actor == "Codex":
        return [codex_bin(), "exec", "-s", "read-only", "--skip-git-repo-check",
                "--ignore-user-config", "-C", project, prompt]
    return [claude_bin(), "-p", prompt]


def _session_path(project):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bridge_common import collab_paths, find_project_root
    return os.path.join(collab_paths(find_project_root(project))["dir"], ".codex_chat_session")


def _load_session(project):
    try:
        with open(_session_path(project)) as f:
            data = json.load(f) or {}
        return data.get("session_id") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save_session(project, sid):
    p = _session_path(project)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"session_id": sid}, f)
    os.replace(tmp, p)


def _thread_id_from_json(stdout):
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "thread.started" and o.get("thread_id"):
            return o["thread_id"]
    return None


def _chat_timeout():
    try:
        return int(os.environ.get(NAMESPACE_TIMEOUT_ENV, "180"))
    except ValueError:
        return 180


def run_codex_chat(prompt, project, image_path=None, _retried=False):
    timeout = _chat_timeout()
    sid = _load_session(project)
    td = tempfile.mkdtemp()
    last = os.path.join(td, "last.txt")
    argv = chat_argv("Codex", prompt, project, image_path=image_path, session_id=sid)
    argv += ["--output-last-message", last]
    try:
        with open(os.devnull) as devnull:
            r = subprocess.run(argv, stdin=devnull, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 and sid and not _retried:
            try:
                os.remove(_session_path(project))
            except OSError:
                pass
            return run_codex_chat(prompt, project, image_path=image_path, _retried=True)
        if r.returncode != 0:
            return ""
        if not sid:
            tid = _thread_id_from_json(r.stdout)
            if tid:
                _save_session(project, tid)
        try:
            with open(last) as f:
                return f.read().strip()
        except OSError:
            return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        shutil.rmtree(td, ignore_errors=True)
