#!/usr/bin/env python3
"""_auto_turn.py — one autonomous coordination turn for claude-codex-bridge.

The DETERMINISTIC harness owns every protocol-critical operation (gates, global
lock, CAS, per-watcher high-water mark, state/signal writes, logging, budget caps,
failure->halt). The MODEL only produces *content* (a draft), because a model can't
be trusted to follow the protocol byte-for-byte.

Flow (matches DESIGN_autonomous_watcher.md v4, draft -> commit-under-lock):
  read signal/state/high-water -> gates -> advance high-water -> run headless
  agent to get a JSON draft (NO file writes by the model) -> acquire global lock
  -> CAS re-check update_id -> append draft to the board outbox, write state,
  write signal LAST -> release lock -> log. Any anomaly -> halt to awaiting_human.

Invoked by watch-collaboration.sh; can also be run once by hand for testing.

Usage:
  _auto_turn.py --as claude|codex [--project DIR] [--allow-write]
                [--lock-ttl SECONDS] [--run-id ID]
Exit codes: 0 acted, 10 skipped (gate not met), 20 halted (awaiting_human), 30 error.
"""
import os
import sys
import json
import time
import errno
import socket
import argparse
import subprocess
import tempfile

OTHER = {"Claude": "Codex", "Codex": "Claude"}
VALID_STATUS = {"active", "paused", "awaiting_human", "done"}
VALID_ACTOR = {"Claude", "Codex", "human", None}

# --- roles (Slice 1: roles foundation) ---
KNOWN_ROLES = {"peer", "planner", "executor", "reviewer", "healer", "improver", "coverer"}
# privilege ordering for monotonic role_change gating (higher = more power)
ROLE_PRIV = {"reviewer": 0, "planner": 0, "peer": 1, "executor": 2,
             "healer": 3, "improver": 3, "coverer": 3}
SPECIAL_ROLES = {"healer", "improver", "coverer"}   # behavior NOT implemented in Slice 1
WRITE_ROLES = {"peer", "executor"}                  # roles that MAY use write tools (if the watcher allows)
REVIEWER_VERDICTS = {"GO", "NO-GO", "REVISE", None}
DEFAULT_ROLE = "peer"


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


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
    path = os.path.join(project, "collaboration_auto.log")
    try:
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
    payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                          "run_id": run_id, "acquired_at": time.time()})
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, payload.encode())
            os.close(fd)
            return True
        except FileExistsError:
            # maybe stale?
            try:
                info = json.load(open(lock_path))
                age = time.time() - float(info.get("acquired_at", 0))
                holder = int(info.get("pid", -1))
                if age > ttl and not _pid_alive(holder):
                    os.unlink(lock_path)  # break stale lock
                    continue
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def release_lock(lock_path):
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
    idx = text.find(header)
    if idx == -1:
        text = text.rstrip() + "\n\n" + header + "\n" + entry
    else:
        nl = text.find("\n", idx)
        nl = len(text) if nl == -1 else nl + 1
        text = text[:nl] + entry + text[nl:]
    atomic_write(board_path, text)


def append_to_outbox(board_path, actor, markdown):
    """Append under '## <actor> Outbox'."""
    _append_under_header(board_path, "## %s Outbox" % actor, markdown)


def read_section(board_path, header_name):
    """Return the text of the '## <header_name>' section, or '' if absent."""
    try:
        text = open(board_path).read()
    except FileNotFoundError:
        return ""
    needle = "## " + header_name
    idx = text.find(needle)
    if idx == -1:
        return ""
    nxt = text.find("\n## ", idx + len(needle))
    return text[idx: (len(text) if nxt == -1 else nxt)].strip()


# ---------- roles (Slice 1) ----------

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

DRAFT_INSTRUCTION = (
    "You are one half of an autonomous two-agent collaboration (Claude + Codex) "
    "coordinated through a shared board. You have been handed a turn. Do your "
    "role's work for THIS turn only, then reply.\n\n"
    "Return ONLY a single JSON object, no prose around it:\n"
    '{"reply_markdown": "<what to append to your outbox: findings/answer/handoff>",'
    ' "next_actor": "Claude|Codex|human|null",'
    ' "status": "active|awaiting_human|done",'
    ' "summary": "<one short line describing what you did>"}\n\n'
    "Rules: set next_actor to the OTHER agent if you need them to act next; to "
    "\"human\" or status \"awaiting_human\" if a human decision is needed; status "
    "\"done\" if the work is complete. Do NOT edit the board, signal, or state "
    "files yourself — the harness commits your reply. Keep it concise.\n\n"
    "Optional fields you MAY add: if your role is 'reviewer', include "
    '\"verdict\": \"GO|NO-GO|REVISE\". To request a role change, include '
    '\"role_change\": {\"actor\": \"Claude|Codex\", \"role\": \"peer|planner|executor|reviewer\"} '
    "— note only a self-downgrade applies immediately; anything else is queued for approval. "
    "If YOU are about to be limited (out of session / context / rate), include "
    '\"handoff\": {\"reason\": \"context_full|session_expiring|rate_limited\"} to hand your '
    "work to the other agent. If you are COVERING and believe the original agent can resume, "
    'you may include \"handback\": true (a human confirms before the loop returns to them).'
)


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


# --- Slice 2: bounded, mechanical self-repair (healer, retry-only) ---

def _clamp_max_repair():
    try:
        return max(0, min(2, int(os.environ.get("BRIDGE_MAX_REPAIR", "2"))))
    except Exception:
        return 2

MAX_REPAIR = _clamp_max_repair()   # clamped 0..2; 0 == today's v4 (every anomaly halts)
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


# ---------- the turn ----------

def _write_failure_state(state_path, reason, extra):
    try:
        state = read_json(state_path, {}) or {}
    except Exception:
        state = {}  # tolerate a corrupt existing state — we overwrite it visibly
    state["status"] = "awaiting_human"
    state["failure"] = dict(reason=reason, at=now_str(), **extra)
    atomic_write_json(state_path, state)


def halt(project, state_path, lock_path, run_id, reason, lock_held=False, **extra):
    """Set awaiting_human and exit 20. State is written ONLY under the lock:
    - lock_held=True: caller already holds the lock (e.g. CAS conflict); write
      directly then release.
    - lock_held=False: acquire the lock first; if a live committer holds it, do
      NOT write (never clobber an in-flight commit) — just log/notify/exit."""
    wrote = False
    if lock_held:
        try:
            _write_failure_state(state_path, reason, extra); wrote = True
        finally:
            release_lock(lock_path)
    else:
        if acquire_lock(lock_path, run_id, ttl=0, wait=10):
            try:
                _write_failure_state(state_path, reason, extra); wrote = True
            finally:
                release_lock(lock_path)
    log_event(project, "halted", run_id=run_id, reason=reason, wrote_state=wrote, **extra)
    notify("Auto-loop halted: %s (awaiting human)" % reason)
    sys.exit(20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="side", required=True, choices=["claude", "codex"])
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--lock-ttl", type=int, default=600)
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    self_actor = "Claude" if a.side == "claude" else "Codex"
    run_id = a.run_id or ("run-%d-%s" % (int(time.time()), self_actor.lower()))

    signal_p = os.path.join(project, "collaboration_signal.json")
    state_p = os.path.join(project, "collaboration_state.json")
    board_p = os.path.join(project, "collaboration.md")
    lock_p = os.path.join(project, "collaboration.lock")
    hw_p = os.path.join(project, ".watcher_%s.state" % a.side)
    sess_p = os.path.join(project, ".watcher_%s.session" % a.side)

    try:
        state = read_json(state_p)
        signal = read_json(signal_p)
        hw = (read_json(hw_p, {}) or {}).get("last_processed_update_id", 0)
    except RuntimeError as e:
        # corrupt signal/state/high-water JSON -> halt to awaiting_human (visible),
        # don't crash silently into the watcher's swallowed exit code.
        halt(project, state_p, lock_p, run_id, "corrupt_json", detail=str(e)[:200])
    if state is None:
        print("no collaboration_state.json -> autonomous mode off"); sys.exit(10)
    if signal is None:
        print("no signal yet"); sys.exit(10)
    uid = signal.get("update_id", 0)

    # ---- gates ----
    if state.get("status") != "active":
        print("status != active (%s)" % state.get("status")); sys.exit(10)
    if uid < hw:
        halt(project, state_p, lock_p, run_id, "update_id_regression", seen=uid, hw=hw)
    if uid <= hw:
        print("no new update (uid=%s hw=%s)" % (uid, hw)); sys.exit(10)
    if state.get("next_actor") != self_actor:
        print("not my turn (next_actor=%s)" % state.get("next_actor")); sys.exit(10)
    if signal.get("updated_by") == self_actor:
        print("own write, ignoring"); sys.exit(10)
    if state.get("turn", 0) >= state.get("max_turns", 12):
        halt(project, state_p, lock_p, run_id, "max_turns_reached",
             turn=state.get("turn"), max_turns=state.get("max_turns"))
    mc = state.get("max_cost_usd")
    if mc is not None and state.get("cost_so_far_usd", 0) >= mc:
        halt(project, state_p, lock_p, run_id, "max_cost_reached",
             cost=state.get("cost_so_far_usd"), max_cost=mc)

    # ---- advance high-water BEFORE running the model (dedupe replayed fs events) ----
    atomic_write_json(hw_p, {"last_processed_update_id": uid})
    seen = uid

    # ---- coverage (Slice 3) + role (Slice 1): pick template + narrow write ----
    roles = state.get("roles", {}) or {}
    resource_profiles = state.get("resource_profiles", {}) or {}
    coverage_owner = state.get("coverage_owner")
    covered_actor = state.get("covered_actor")
    start_epoch = state.get("handoff_epoch", 0)
    is_covering = (coverage_owner == self_actor and covered_actor in ("Claude", "Codex"))
    if is_covering:
        # Covering for the peer: use the coverer role; write permission comes from
        # the COVERED actor's original role (a read-only covered role stays
        # read-only). The coverer role itself grants no extra write.
        my_role = "coverer"
        role_write_ok = bool(a.allow_write) and (roles.get(covered_actor, DEFAULT_ROLE) in WRITE_ROLES)
    else:
        my_role = roles.get(self_actor, DEFAULT_ROLE)
        if my_role not in KNOWN_ROLES:
            my_role = DEFAULT_ROLE
        # Permission ceiling = watcher --allow-write; a role can only NARROW it,
        # never widen it. Enforced here in the harness, not by the prompt.
        role_write_ok = bool(a.allow_write) and (my_role in WRITE_ROLES)
    role_tmpl = load_role_template(project, my_role)

    # Slice 3: cover-budget gate BEFORE spending a model call. If coverage is
    # already exhausted (e.g. state was manually/abnormally resumed), do NOT run
    # the model — halt to awaiting_human.
    if is_covering and state.get("cover_turns_used", 0) >= state.get("max_cover_turns", 1):
        halt(project, state_p, lock_p, run_id, "cover_budget_exhausted",
             used=state.get("cover_turns_used", 0), max=state.get("max_cover_turns", 1))

    changed = signal.get("changed_section", "")
    section_text = read_section(board_p, changed) if changed else ""
    envelope = {
        "run_id": run_id, "update_id": uid, "you_are": self_actor, "your_role": my_role,
        "changed_section": changed, "summary_from_peer": signal.get("summary", ""),
        "commit_contract": "harness_commits_your_draft_under_lock",
        "allow_project_writes": role_write_ok,
        "covering_for": covered_actor if is_covering else None,
        "resource_profiles": resource_profiles,
        "your_resource_profile": resource_profiles.get(self_actor, {}),
    }
    role_preamble = (("YOUR ROLE THIS TURN: %s\n%s\n\n" % (my_role, role_tmpl)) if role_tmpl
                     else ("YOUR ROLE THIS TURN: %s\n\n" % my_role))
    prompt = (role_preamble + DRAFT_INSTRUCTION + "\n\nTURN ENVELOPE:\n"
              + json.dumps(envelope, ensure_ascii=False)
              + "\n\nThe section that just changed (" + (changed or "n/a") + "):\n"
              + (section_text or "(empty)")
              + "\n\nAlso read collaboration.md / the project as needed for context.")

    log_event(project, "started", run_id=run_id, actor=self_actor, role=my_role,
              update_id=uid, changed_section=changed)

    # ---- run the model with bounded mechanical self-repair (Slice 2) ----
    # NO lock held; the model only produces a draft. Only the model call and a
    # single malformed-JSON re-prompt are inside this loop — never gates, CAS,
    # high-water, or commit. With MAX_REPAIR=0 this is exactly v4 (every anomaly
    # halts, no re-prompt).
    def _invoke(p):
        return (run_claude if a.side == "claude" else run_codex)(p, project, role_write_ok, sess_p)

    draft = cost = None
    attempt = 0          # transient retries used
    total_wait = 0.0
    reprompted = False
    cur_prompt = prompt
    while True:
        try:
            draft, cost = _invoke(cur_prompt)
            if attempt or reprompted:
                log_event(project, "repaired", run_id=run_id, attempts=attempt, reprompted=reprompted)
            break
        except subprocess.TimeoutExpired:
            halt(project, state_p, lock_p, run_id, "agent_timeout")     # fatal: never retry a long timeout
        except MalformedDraft as e:
            if reprompted or MAX_REPAIR == 0:
                halt(project, state_p, lock_p, run_id,
                     "repeated_malformed_drafts" if reprompted else "malformed_draft", detail=str(e)[:160])
            reprompted = True
            log_event(project, "repair_attempt", run_id=run_id, kind="malformed_draft")
            cur_prompt = (prompt + "\n\nIMPORTANT: your previous reply was NOT valid JSON. "
                          "Return ONLY the single JSON object specified above, with no prose, "
                          "no code fences, nothing else.")
        except TransientError as e:
            ra = e.retry_after
            if ra is not None and ra > MAX_SINGLE_WAIT:
                halt(project, state_p, lock_p, run_id, "rate_limited_too_long", cls=e.cls, retry_after=ra)
            if attempt >= MAX_REPAIR:
                halt(project, state_p, lock_p, run_id, "repair_exhausted", cls=e.cls, detail=str(e)[:160])
            wait = min(MAX_SINGLE_WAIT, ra if ra else float(2 ** attempt))
            if total_wait + wait > MAX_TOTAL_WAIT:
                halt(project, state_p, lock_p, run_id, "repair_exhausted", cls=e.cls, detail="retry wait budget exceeded")
            attempt += 1
            log_event(project, "repair_attempt", run_id=run_id, n=attempt, kind="transient", cls=e.cls, wait=wait)
            time.sleep(wait)
            total_wait += wait
        except Exception as e:
            halt(project, state_p, lock_p, run_id, "agent_error", detail=str(e)[:200])

    # validate draft
    nxt = draft.get("next_actor")
    nxt = None if nxt in (None, "null", "") else nxt
    st_new = draft.get("status", "active")
    if nxt not in VALID_ACTOR or st_new not in VALID_STATUS:
        halt(project, state_p, lock_p, run_id, "invalid_draft",
             next_actor=str(nxt), status=str(st_new))
    if mc is not None and cost is None and a.side == "claude":
        halt(project, state_p, lock_p, run_id, "cost_unparseable")
    # role-specific output contract (Slice 1): reviewer may carry a verdict
    verdict = draft.get("verdict")
    if my_role == "reviewer" and verdict not in REVIEWER_VERDICTS:
        halt(project, state_p, lock_p, run_id, "invalid_draft", role="reviewer", verdict=str(verdict))
    # classify any role_change request now; it is APPLIED under the lock below
    role_req = draft.get("role_change")
    role_decision = classify_role_change(self_actor, role_req, roles) if role_req else None
    # Slice 3 coverage intents (resolved under the lock below)
    handoff_req = draft.get("handoff") if isinstance(draft.get("handoff"), dict) else None
    handback_req = bool(draft.get("handback")) and is_covering
    opening_handoff = bool(handoff_req) and not is_covering   # you hand off your OWN turn

    # ---- acquire lock and commit (CAS) ----
    # High-water was already advanced; if we can't commit we must NOT silently
    # skip (that would lose this turn) -> halt to awaiting_human.
    if not acquire_lock(lock_p, run_id, ttl=a.lock_ttl, wait=60):
        halt(project, state_p, lock_p, run_id, "lock_unavailable", seen=seen)
    cur = read_json(signal_p) or {}
    if cur.get("update_id") != seen:
        # CAS conflict — write failure UNDER the held lock (halt releases it).
        halt(project, state_p, lock_p, run_id, "cas_conflict",
             lock_held=True, seen=seen, now=cur.get("update_id"))
    cur_state = read_json(state_p) or {}
    if cur_state.get("status") != "active" or cur_state.get("next_actor") != self_actor:
        release_lock(lock_p)
        log_event(project, "authority_changed", run_id=run_id); sys.exit(10)
    # Slice 3 coverage epoch guard: a covering turn must commit against the same
    # coverage it opened under (belt & suspenders atop the update_id CAS).
    if is_covering and (cur_state.get("handoff_epoch", 0) != start_epoch
                        or cur_state.get("coverage_owner") != self_actor):
        halt(project, state_p, lock_p, run_id, "coverage_changed", lock_held=True,
             seen_epoch=start_epoch, now_epoch=cur_state.get("handoff_epoch"))
    try:
        new_uid = seen + 1
        common = {
            "turn": cur_state.get("turn", 0) + 1, "last_writer": self_actor,
            "last_update_id": new_uid, "run_id": run_id,
            "cost_so_far_usd": round(cur_state.get("cost_so_far_usd", 0) + (cost or 0), 6),
        }
        if is_covering:
            # COVERER turn (single-turn MVP): write the Coverage Log (NEVER the
            # covered actor's outbox), bump the counter, pause for a human.
            _append_under_header(board_p, "## Coverage Log", "(%s covering %s) %s"
                                 % (self_actor, covered_actor, draft.get("reply_markdown", "")))
            reason = "handback_suggested" if handback_req else "cover_turn_done"
            cur_state.update(common)
            cur_state.update({"next_actor": "human", "status": "awaiting_human",
                              "cover_turns_used": cur_state.get("cover_turns_used", 0) + 1,
                              "failure": {"reason": reason, "at": now_str()}})
            sig_section = "Coverage Log"
            log_event(project, "covering", run_id=run_id, actor=self_actor, covered=covered_actor,
                      used=cur_state["cover_turns_used"], reason=reason)
        elif opening_handoff:
            # OPEN coverage: hand my OWN turn to the peer (self-authorizing).
            append_to_outbox(board_p, self_actor, draft.get("reply_markdown", ""))
            peer = OTHER[self_actor]
            cur_state.update(common)
            cur_state.update({"next_actor": peer, "status": "active",
                              "coverage_owner": peer, "covered_actor": self_actor,
                              "handoff_epoch": cur_state.get("handoff_epoch", 0) + 1,
                              "handoff_reason": handoff_req.get("reason"),
                              "cover_turns_used": 0, "failure": None})
            sig_section = "%s Outbox" % self_actor
            log_event(project, "handoff", run_id=run_id, actor=self_actor, to=peer,
                      reason=handoff_req.get("reason"))
        else:
            # NORMAL turn.
            append_to_outbox(board_p, self_actor, draft.get("reply_markdown", ""))
            cur_state.update(common)
            cur_state.update({"next_actor": nxt if nxt is not None else OTHER[self_actor],
                              "status": st_new, "failure": None})
            sig_section = "%s Outbox" % self_actor
            if role_decision:  # Slice 1: self-downgrade applies; else pending/reject
                kind, info = role_decision
                if kind == "apply":
                    cur_state.setdefault("roles", dict(roles or {}))[self_actor] = info
                    log_event(project, "role_applied", run_id=run_id, actor=self_actor, new_role=info)
                elif kind == "pending":
                    cur_state["pending_role_change"] = {
                        "requested_by": self_actor, "actor": role_req.get("actor"),
                        "role": role_req.get("role"), "reason": info, "at": now_str()}
                    log_event(project, "role_change_pending", run_id=run_id, requested_by=self_actor,
                              actor=role_req.get("actor"), role=role_req.get("role"), reason=info)
                else:
                    log_event(project, "role_change_rejected", run_id=run_id,
                              requested_by=self_actor, target=role_req, reason=info)
        atomic_write_json(state_p, cur_state)
        atomic_write_json(signal_p, {  # signal written LAST = commit marker
            "update_id": new_uid, "updated_at": now_str(), "updated_by": self_actor,
            "changed_section": sig_section, "summary": draft.get("summary", "")[:200]})
        atomic_write_json(hw_p, {"last_processed_update_id": new_uid})  # our own write
    except Exception as e:
        halt(project, state_p, lock_p, run_id, "commit_error", lock_held=True, detail=str(e)[:200])
    finally:
        release_lock(lock_p)

    log_event(project, "committed", run_id=run_id, actor=self_actor, update_id=new_uid,
              next_actor=cur_state["next_actor"], status=cur_state["status"],
              turn=cur_state["turn"], cost=cost, cost_so_far=cur_state["cost_so_far_usd"])
    if cur_state["status"] != "active":
        notify("Auto-loop %s after %s's turn (turn %d)" % (cur_state["status"], self_actor, cur_state["turn"]))
    sys.exit(0)


if __name__ == "__main__":
    main()
