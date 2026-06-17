#!/usr/bin/env python3
"""bridge_common.py — shared primitives for the claude-codex-bridge harnesses.

Extracted (behavior-preserving) from _auto_turn.py so _auto_turn.py (2-actor),
_queue_turn.py (multi-agent), and _compact.py all depend on ONE module of shared
primitives instead of importlib-loading each other. Holds: atomic file I/O, the
global lock, logging/notify, board section helpers, role constants + templates,
the model runners (run_claude/run_codex) and their rate-limit/JSON helpers.
"""
import os
import sys
import json
import time
import errno
import socket
import subprocess
import tempfile
import re

OTHER = {"Claude": "Codex", "Codex": "Claude"}
VALID_STATUS = {"active", "paused", "awaiting_human", "done"}
VALID_ACTOR = {"Claude", "Codex", "human", None}

# --- roles ---
KNOWN_ROLES = {"peer", "planner", "executor", "reviewer", "healer", "improver", "coverer"}
# privilege ordering for monotonic role_change gating (higher = more power)
ROLE_PRIV = {"reviewer": 0, "planner": 0, "peer": 1, "executor": 2,
             "healer": 3, "improver": 3, "coverer": 3}
SPECIAL_ROLES = {"healer", "improver", "coverer"}   # gated roles; coverer/improver implemented, healer is mechanical-retry not a role
WRITE_ROLES = {"peer", "executor"}                  # roles that MAY use write tools (if the watcher allows)
REVIEWER_VERDICTS = {"GO", "NO-GO", "REVISE", None}
DEFAULT_ROLE = "peer"


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


# ---------- project root + coordination-layer paths (single source of truth) ----------

def find_project_root(start=None):
    """Walk UP from `start` (default cwd). Per ancestor, in order:
    - contains '.collab/' -> return it (initialized project).
    - contains '.git/' (without .collab/) -> return it as the project root and
      STOP — never cross a nested git/submodule boundary to a parent .collab/.
    - else keep walking up. None found -> return the starting dir.
    Explicit paths from a caller's --project always take precedence over this."""
    cur = os.path.abspath(start or os.getcwd())
    start_abs = cur
    while True:
        if os.path.isdir(os.path.join(cur, ".collab")):
            return cur
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return start_abs
        cur = parent


def collab_paths(root):
    """All coordination-layer file paths for a project root, under <root>/.collab/.
    Single source of truth — every script must derive paths from here so the
    board/signal/state/queue/lock/high-water all relocate together (no split-brain).
    Back-compat: if <root>/.collab/ is absent but any legacy flat coordination
    file exists at the root, fall back to the flat layout (callers should warn)."""
    _flat_markers = ("collaboration.md", "collaboration_state.json",
                     "collaboration_signal.json", "collaboration_queue.json")
    legacy_flat = (not os.path.isdir(os.path.join(root, ".collab"))
                   and any(os.path.exists(os.path.join(root, m)) for m in _flat_markers))
    base = root if legacy_flat else os.path.join(root, ".collab")
    return {
        "root": root, "dir": base, "legacy_flat": legacy_flat,
        "board": os.path.join(base, "collaboration.md"),
        "signal": os.path.join(base, "collaboration_signal.json"),
        "state": os.path.join(base, "collaboration_state.json"),
        "queue": os.path.join(base, "collaboration_queue.json"),
        "participants": os.path.join(base, "collaboration_participants.json"),
        "reviews": os.path.join(base, "collaboration_reviews.json"),
        "chat_delivery": os.path.join(base, "chat_delivery.json"),
        "chat_typing": os.path.join(base, "chat_typing.json"),
        "log": os.path.join(base, "collaboration_auto.log"),
        "archive": os.path.join(base, "collaboration_archive"),
        "lock": os.path.join(base, "collaboration.lock"),
        # Handshake is a SEPARATE channel from the signal: a liveness ping/pong the
        # peer's board-wait ACKs at the harness layer. It must never touch
        # collaboration_signal.json's update_id (that means "real content changed").
        # Guarded by its own small lock, not the main collaboration.lock.
        "handshake": os.path.join(base, "collaboration_handshake.json"),
        "handshake_lock": os.path.join(base, "collaboration_handshake.lock"),
    }


# ---------- file helpers (atomic) ----------

def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        raise RuntimeError("corrupt JSON in %s: %s" % (path, e))


def atomic_write(path, text):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------- logging / notify ----------

LOG_MAX_BYTES = int(os.environ.get("BRIDGE_LOG_MAX_BYTES", str(5 * 1024 * 1024)))


def log_event(project, event, **fields):
    path = collab_paths(project)["log"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")  # single-generation rotation
        rec = {"ts": now_str(), "event": event}
        rec.update(fields)
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def notify(text):
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            'display notification %s with title "claude-codex-bridge"'
                            % json.dumps(text)], timeout=5)
        else:
            subprocess.run(["notify-send", "claude-codex-bridge", text], timeout=5)
    except Exception:
        pass


# ---------- global lock (atomic create + stale handling) ----------

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno != errno.ESRCH


def acquire_lock(lock_path, run_id, ttl, wait=0):
    deadline = time.time() + wait
    while True:
        try:
            payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                                  "run_id": run_id, "acquired_at": time.time()})
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, payload.encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Maybe stale? Break it ATOMICALLY. The old read->unlink->create break let
            # two waiters both judge the same lock stale, both unlink, then both create a
            # fresh lock -> both believe they hold it (TOCTOU double-hold). Instead, CLAIM
            # the break by renaming the stale lock to a unique name: os.rename of a given
            # source succeeds for exactly one racer; the loser hits an OSError and just
            # retries the loop, where it finds the winner's fresh (non-stale) lock.
            try:
                with open(lock_path) as _lf:
                    info = json.load(_lf)
                age = time.time() - float(info.get("acquired_at", 0))
                holder = int(info.get("pid", -1))
                if age > ttl and not _pid_alive(holder):
                    breaking = "%s.breaking.%s" % (lock_path, run_id)
                    try:
                        os.rename(lock_path, breaking)
                    except OSError:
                        continue  # another racer won the break; retry
                    try:
                        os.unlink(breaking)
                    except FileNotFoundError:
                        pass
                    continue  # next iteration creates a fresh lock atomically
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def release_lock(lock_path, run_id=None):
    # Owner-checked: only remove the lock if we still hold it (its run_id matches ours).
    # This stops a late release from a former holder deleting a DIFFERENT holder's fresh
    # lock. run_id=None keeps the legacy unconditional unlink for callers not yet updated.
    if run_id is not None:
        try:
            with open(lock_path) as _lf:
                if json.load(_lf).get("run_id") != run_id:
                    return  # not ours (or already replaced) -> leave it alone
        except FileNotFoundError:
            return
        except Exception:
            return  # unreadable/corrupt -> can't confirm ownership, leave for stale-break
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass


# ---------- board section append ----------

def _append_under_header(board_path, header, markdown):
    """Append markdown under a '## <header>' section (create the section if absent)."""
    entry = "\n### %s\n\n%s\n" % (now_str(), markdown.strip())
    try:
        with open(board_path) as f:
            text = f.read()
    except FileNotFoundError:
        text = "# Agent Collaboration Board\n"
    # Anchor to a FULL header line so "## Chat" can't match "## Chat Archive"
    # (substring find would insert into the look-alike section). Trailing
    # whitespace on the header line is tolerated.
    m = re.search(r'(?m)^' + re.escape(header) + r'[ \t]*$', text)
    if not m:
        text = text.rstrip() + "\n\n" + header + "\n" + entry
    else:
        nl = text.find("\n", m.start())
        nl = len(text) if nl == -1 else nl + 1
        text = text[:nl] + entry + text[nl:]
    atomic_write(board_path, text)


def append_to_outbox(board_path, actor, markdown):
    """Append under '## <actor> Outbox'."""
    _append_under_header(board_path, "## %s Outbox" % actor, markdown)


def read_section(board_path, header_name):
    """Return the text of the '## <header_name>' section, or '' if absent."""
    try:
        with open(board_path) as f:
            text = f.read()
    except FileNotFoundError:
        return ""
    m = re.search(r'(?m)^## ' + re.escape(header_name) + r'[ \t]*$', text)
    if not m:
        return ""
    nxt = re.search(r'(?m)^## ', text[m.end():])
    end = len(text) if not nxt else m.end() + nxt.start()
    return text[m.start():end].strip()


# ---------- roles ----------

def load_role_template(project, role):
    """Role prompt template: project override first, then the shipped templates,
    else empty (base instruction only). Templates carry NO permission authority."""
    candidates = [
        os.path.join(project, "role_templates", role + ".md"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "role_templates", role + ".md"),
    ]
    for c in candidates:
        try:
            with open(c) as f:
                return f.read().strip()
        except (FileNotFoundError, NotADirectoryError):
            continue
    return ""


def classify_role_change(self_actor, request, roles):
    """Deterministic policy. Returns (decision, info):
      ('apply', role)   self-downgrade to a non-special role with priv <= current
      ('pending', why)  upgrade / peer change / entering a special role -> needs
                        reviewer-or-human approval (NOT auto-applied)
      ('reject', why)   malformed
    A role change can never grant tools the watcher wasn't started with — that
    ceiling is enforced separately when computing effective write permission."""
    if not isinstance(request, dict):
        return ("reject", "malformed")
    actor = request.get("actor")
    role = request.get("role")
    if actor not in ("Claude", "Codex") or role not in KNOWN_ROLES:
        return ("reject", "unknown_actor_or_role")
    cur_priv = ROLE_PRIV.get((roles or {}).get(actor, DEFAULT_ROLE), ROLE_PRIV[DEFAULT_ROLE])
    new_priv = ROLE_PRIV.get(role, ROLE_PRIV[DEFAULT_ROLE])
    if actor == self_actor and role not in SPECIAL_ROLES and new_priv <= cur_priv:
        return ("apply", role)
    return ("pending", "needs_approval")


# ---------- model invocation ----------

def _extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # find outermost {...}
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError("no JSON object in model output")


# --- bounded, mechanical self-repair (healer, retry-only) ---

def _clamp_max_repair():
    try:
        return max(0, min(2, int(os.environ.get("BRIDGE_MAX_REPAIR", "2"))))
    except Exception:
        return 2

MAX_REPAIR = _clamp_max_repair()   # clamped 0..2; 0 == every anomaly halts
MAX_SINGLE_WAIT = 60               # seconds — cap on a single backoff / retry-after sleep
MAX_TOTAL_WAIT = 120               # seconds — cap on total retry sleep per turn
_RATE_MARKERS = ("rate limit", "rate_limit", "429", "too many requests", "overloaded")


class TransientError(Exception):
    """A recoverable model-call failure (launch-level / rate-limited)."""
    def __init__(self, msg, retry_after=None, cls="transient"):
        super().__init__(msg)
        self.retry_after = retry_after
        self.cls = cls


class MalformedDraft(Exception):
    """The model returned something that isn't a parseable JSON draft."""


def _looks_rate_limited(text):
    return any(m in text for m in _RATE_MARKERS)


def _parse_retry_after(text):
    import re
    m = re.search(r"retry[-_ ]?after[\"'=:\s]+(\d+)", text)
    return int(m.group(1)) if m else None


def run_claude(prompt, project, allow_write, sess_path):
    claude = os.environ.get("CLAUDE_BIN") or "claude"
    tools = ["Read", "Grep", "Glob"] + (["Edit", "Write"] if allow_write else [])
    cmd = [claude, "-p", prompt, "--output-format", "json",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--permission-mode", "acceptEdits" if allow_write else "default",
           "--add-dir", project]
    sid = (read_json(sess_path, {}) or {}).get("claude_session_id")
    if sid:
        cmd += ["--resume", sid]
    cmd += ["--allowedTools"] + tools
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(os.environ.get("BRIDGE_TURN_TIMEOUT", "900")), cwd=project)
    except FileNotFoundError as e:
        raise TransientError("claude launch failed: %s" % e, cls="launch_failure")
    # Success path FIRST. Never scan a successful draft for rate markers — the
    # draft text may legitimately mention "rate limit"/"429"/"overloaded".
    try:
        data = json.loads(p.stdout)
        draft = _extract_json(data.get("result", ""))
    except Exception:
        blob = (p.stderr or "").lower()
        if p.returncode != 0:
            blob += " " + (p.stdout or "").lower()
        if _looks_rate_limited(blob):
            raise TransientError("claude rate-limited", _parse_retry_after(blob), cls="rate_limit")
        raise MalformedDraft("claude result is not a parseable draft JSON")
    cost = data.get("total_cost_usd")
    new_sid = data.get("session_id")
    if new_sid:
        st = read_json(sess_path, {}) or {}
        st["claude_session_id"] = new_sid
        atomic_write_json(sess_path, st)
    return draft, cost


def run_codex(prompt, project, allow_write, sess_path):
    # The model is instructed to return the JSON draft as its final message; we
    # read it via --output-last-message and parse it (symmetric to the Claude
    # side). NOTE: --output-schema is intentionally NOT used — codex exec exits
    # non-zero with it in testing. --ignore-user-config keeps the spawned Codex
    # fast and free of the user's MCP stack (no recursion into claude_chat).
    codex = os.environ.get("CODEX_BIN") or "codex"
    tmpd = tempfile.mkdtemp()
    last_f = os.path.join(tmpd, "last.json")
    sandbox = "workspace-write" if allow_write else "read-only"
    # NOTE: `codex exec resume` rejects the global -C/-s options in the current
    # CLI, so resume is intentionally NOT used. Codex continuity comes from the
    # shared board/digest it reads each turn. (Future: capture the session id and
    # resume by id with the correct argument order.)
    cmd = [codex, "exec", prompt, "--output-last-message", last_f, "-C", project,
           "--skip-git-repo-check", "--ignore-user-config", "-s", sandbox]
    try:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=int(os.environ.get("BRIDGE_TURN_TIMEOUT", "900")), cwd=project)
        except FileNotFoundError as e:
            raise TransientError("codex launch failed: %s" % e, cls="launch_failure")
        # Success path FIRST — never scan a successful final message for rate markers.
        if os.path.exists(last_f):
            try:
                with open(last_f) as f:
                    # cost not reliably parseable -> None; max_turns governs the Codex side
                    return _extract_json(f.read()), None
            except (ValueError, json.JSONDecodeError):
                pass  # fall through to failure classification
        blob = (p.stderr or "").lower()
        if p.returncode != 0:
            blob += " " + (p.stdout or "").lower()
        if _looks_rate_limited(blob):
            raise TransientError("codex rate-limited", _parse_retry_after(blob), cls="rate_limit")
        if not os.path.exists(last_f):
            raise RuntimeError("codex exec produced no final message (exit %s): %s"
                               % (p.returncode, (p.stderr or "")[-200:]))
        raise MalformedDraft("codex final message is not a parseable draft JSON")
    finally:
        try:
            import shutil as _sh; _sh.rmtree(tmpd, ignore_errors=True)
        except Exception:
            pass


# Names re-exported via `from bridge_common import *` (includes _-prefixed names).
__all__ = [
    "OTHER", "VALID_STATUS", "VALID_ACTOR",
    "KNOWN_ROLES", "ROLE_PRIV", "SPECIAL_ROLES", "WRITE_ROLES", "REVIEWER_VERDICTS", "DEFAULT_ROLE",
    "find_project_root", "collab_paths",
    "now_str", "read_json", "atomic_write", "atomic_write_json",
    "LOG_MAX_BYTES", "log_event", "notify",
    "_pid_alive", "acquire_lock", "release_lock",
    "_append_under_header", "append_to_outbox", "read_section",
    "load_role_template", "classify_role_change",
    "_extract_json", "_clamp_max_repair", "MAX_REPAIR", "MAX_SINGLE_WAIT", "MAX_TOTAL_WAIT", "_RATE_MARKERS",
    "TransientError", "MalformedDraft", "_looks_rate_limited", "_parse_retry_after",
    "run_claude", "run_codex",
]
