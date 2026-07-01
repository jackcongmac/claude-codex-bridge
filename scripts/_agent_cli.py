"""_agent_cli.py - actor->capability registry: how each actor is spawned for chat / implement /
review. Keeps codex/claude invocation OUT of the call sites so roles can rotate (roles-nominal)
and so the chat path can use a warm resumed session (P0.5 latency). Argv builders only - the
running/timeout/stdin=/dev/null is the caller's job."""
import os

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
