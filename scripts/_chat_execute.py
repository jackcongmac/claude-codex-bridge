"""Chat-driven execution — P0a deterministic core. Turns a human chat message into a
gated, reported execution decision. LLM judgment and the write-capable executor are
injected boundaries (see execute_once). Code execution is gated behind
BRIDGE_CHAT_EXECUTE=1; signed requirement capture is always on."""
import argparse
import base64
import binascii
import hashlib
import importlib.util as _ilu
import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    _pid_alive,
    acquire_lock,
    atomic_write,
    atomic_write_json,
    collab_paths,
    find_project_root,
    now_str,
    read_json,
    release_lock,
)
import _chat_roles  # noqa: E402
import _sig  # noqa: E402
from _post import post as _board_post   # noqa: E402
from _chat_respond import _image_path_for  # noqa: E402
import _chat_executor  # noqa: E402
import _chat_judge  # noqa: E402

_HIGH_RISK = re.compile(
    r"(发版|发布|publish|release|打?\s*tag\b|删\s*(除|文件|掉)|\bdelete\b|\brm\b|"
    r"force[-\s]?push|--force|git\s+reset\s+--hard\b|reset\s+--hard\b|"
    r"git\s+clean\b|clean\s+-fdx\b|(?<![-\w])drop(?![-\w])|\bwipe\b|\berase\b|"
    r"抹除|清空|清除|\bdeploy\b|部署|\bprod(?:uction)?\b|线上|生产|"
    r"\bsecret\b|token\s+rotation|轮换密钥|\brotate\b)", re.I)
_GREENLIGHT = re.compile(
    r"(确认|同意|批准|可以|开始|执行|继续|\bgo\b|\bgreenlight\b|\byes\b|\bok(?:ay)?\b|"
    r"\bproceed\b)",
    re.I)


def is_high_risk(task_text):
    return bool(_HIGH_RISK.search(task_text or ""))


def _is_greenlight_reply(text):
    return bool(_GREENLIGHT.search(text or ""))


def _human_sig_ok(project, msg):
    if os.environ.get("BRIDGE_REQUIRE_SIGNATURES", "1") == "0":
        return True
    sig_b64 = msg.get("sig")
    if not sig_b64:
        return False
    try:
        raw = base64.b64decode(sig_b64).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError):
        return False
    return _sig.verify(msg.get("speaker", ""), _sig.chat_payload(msg), raw, project=project)


def decide(project, msgs, judge):
    if not msgs:
        return {"action": "none"}
    latest = msgs[-1]
    if not _chat_roles.is_human(latest.get("speaker", ""), project):
        return {"action": "ignore", "reason": "not-human"}
    if not _human_sig_ok(project, latest):
        return {"action": "ignore", "reason": "unsigned-human"}
    image_path = _image_path_for(project, latest)
    pending = _pending_greenlight(project)
    if pending and _is_greenlight_reply(latest.get("text", "")):
        return {"action": "execute", "task": pending["task"], "image_path": image_path}
    verdict = judge(latest.get("text", ""), msgs[:-1], image_path) or {}
    kind = verdict.get("kind")
    if kind == "actionable":
        task = (verdict.get("task") or latest.get("text", "")).strip()
        if is_high_risk(task) or is_high_risk(latest.get("text", "")):
            return {"action": "request_greenlight", "task": task, "image_path": image_path}
        return {"action": "execute", "task": task, "image_path": image_path}
    if kind == "record_requirement":
        task = (verdict.get("task") or latest.get("text", "")).strip()
        return {"action": "record", "task": task}
    if kind == "ambiguous":
        return {"action": "ask",
                "question": verdict.get("question") or "你是要现在就开始做吗?"}
    return {"action": "ignore", "reason": "opinion"}


def dispatch(project, decision, poster, enqueue):
    action = decision.get("action")
    if action == "execute":
        poster("开始执行:%s" % decision["task"])   # ack-before-act, strictly first
        enqueue(decision["task"])
        return "acked-enqueued"
    if action == "request_greenlight":
        task = decision.get("task", "")
        _upsert_task_item(project, task, "awaiting_greenlight")
        _set_pending_greenlight(project, task)
        poster("这个需要你点头才能做(高风险):%s" % task)
        return "requested-greenlight"
    if action == "record":
        task = decision.get("task", "")
        _upsert_task_item(project, task, "open")
        poster("已记录:%s" % task)
        return "recorded"
    if action == "ask":
        poster(decision.get("question") or "你是要现在就开始做吗?")
        return "asked"
    return "noop"


def _capture_only(project, decision, poster):
    action = decision.get("action")
    if action in ("execute", "request_greenlight"):
        task = decision.get("task", "")
        _upsert_task_item(project, task, "open")
        _clear_pending_greenlight(project, task)
        poster("已记入清单(执行器未开):%s" % task)
        return "captured"
    if action == "record":
        task = decision.get("task", "")
        _upsert_task_item(project, task, "open")
        poster("已记录:%s" % task)
        return "recorded"
    if action == "ask":
        poster(decision.get("question") or "你是要现在就开始做吗?")
        return "asked"
    return "ignore"


def _issues_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "ISSUES.md")


def _issues_lock_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "ISSUES.lock")


def _execute_state_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"],
                        "chat_execute_state.json")


def _execute_lock_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"],
                        "chat_execute_state.lock")


_STATE_LIMIT = 500
_VALID_MSG_STATES = {"claimed", "running", "done"}
_CLAIM_STALE_SECONDS = 2000
_GREENLIGHT_STALE_SECONDS = 24 * 60 * 60
_TASK_SECTION = "## Chat-Driven Tasks"


def _read_issues(project):
    try:
        with open(_issues_path(project)) as f:
            return f.read()
    except OSError:
        return ""


def _write_issues(project, body):
    atomic_write(_issues_path(project), body.rstrip() + "\n")


def _update_issues(project, editor):
    run_id = "issues-%s-%s" % (os.getpid(), uuid.uuid4().hex)
    lock_path = _issues_lock_path(project)
    if not acquire_lock(lock_path, run_id, ttl=30, wait=3):
        raise RuntimeError("ISSUES.md lock busy")
    try:
        body = _read_issues(project)
        updated = editor(body)
        if updated is not None:
            _write_issues(project, updated)
        return updated
    finally:
        release_lock(lock_path, run_id)


def _task_id(task):
    normalized = " ".join((task or "").split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _task_line(task, status, result=None):
    result = result or {}
    done = status == "done"
    label = {
        "open": "待办",
        "awaiting_greenlight": "等待确认",
        "running": "进行中",
        "done": "完成",
        "failed": "失败",
    }.get(status, status)
    details = []
    summary = result.get("summary") or ""
    commit = result.get("commit") or ""
    if summary:
        details.append(summary)
    if commit:
        details.append("commit " + commit)
    suffix = (" · " + " · ".join(details)) if details else ""
    return "- [%s] <!-- chat-task:%s --> %s — %s%s" % (
        "x" if done else " ", _task_id(task), label, task, suffix)


def _task_has_status(project, task, status):
    # Follow-up: ISSUES.md task status is unauthenticated; approvals stay gated by signed chat state.
    label = {
        "open": "待办",
        "awaiting_greenlight": "等待确认",
        "running": "进行中",
        "done": "完成",
        "failed": "失败",
    }.get(status, status)
    marker = "<!-- chat-task:%s -->" % _task_id(task)
    pattern = r"(?m)^- \[[ x]\] %s %s — " % (re.escape(marker), re.escape(label))
    return bool(re.search(pattern, _read_issues(project)))


def _upsert_task_item_in_body(body, task, status, result=None):
    line = _task_line(task, status, result=result)
    marker = "<!-- chat-task:%s -->" % _task_id(task)
    marker_re = re.compile(r"(?m)^- \[[ x]\] %s .*$" % re.escape(marker))

    if marker_re.search(body):
        return marker_re.sub(lambda _m: line, body)

    if _TASK_SECTION not in body:
        prefix = body.rstrip()
        return ("%s\n\n%s\n\n%s\n" % (prefix, _TASK_SECTION, line)) if prefix else (
            "%s\n\n%s\n" % (_TASK_SECTION, line))

    section = re.search(r"(?m)^%s\s*$" % re.escape(_TASK_SECTION), body)
    if not section:
        return body.rstrip() + "\n\n%s\n\n%s\n" % (_TASK_SECTION, line)
    next_section = re.search(r"(?m)^## ", body[section.end():])
    insert_at = section.end() + (
        next_section.start() if next_section else len(body[section.end():]))
    prefix = body[:insert_at].rstrip()
    suffix = body[insert_at:].lstrip("\n")
    updated = prefix + "\n" + line + "\n"
    if suffix:
        updated += "\n" + suffix
    return updated


def _upsert_task_item(project, task, status, result=None):
    _update_issues(project, lambda body: _upsert_task_item_in_body(
        body, task, status, result=result))


def _normalize_pending_greenlight(data):
    if not isinstance(data, dict):
        return None
    pending = data.get("pending_greenlight")
    if not isinstance(pending, dict):
        return None
    task = pending.get("task")
    if not isinstance(task, str) or not task.strip():
        return None
    normalized = {
        "task": task,
        "id": pending.get("id") if isinstance(pending.get("id"), str) else _task_id(task),
        "at": pending.get("at") if isinstance(pending.get("at"), str) else "",
    }
    try:
        normalized["epoch"] = float(pending.get("epoch"))
    except (TypeError, ValueError):
        pass
    return normalized


def _state_with_pending(data, messages):
    state = {"version": 2, "messages": messages}
    pending = _normalize_pending_greenlight(data)
    if pending:
        state["pending_greenlight"] = pending
    return state


def _normalize_state(data):
    messages = {}
    if isinstance(data, dict) and isinstance(data.get("handled"), list):
        for mid in data.get("handled", []):
            if isinstance(mid, str) and mid not in messages:
                messages[mid] = {"state": "done", "at": ""}
        return _state_with_pending(data, messages)

    if isinstance(data, dict) and isinstance(data.get("messages"), dict):
        for mid, entry in data.get("messages", {}).items():
            if not isinstance(mid, str):
                continue
            if isinstance(entry, dict):
                state = entry.get("state")
                at = entry.get("at", "")
            else:
                state = entry
                at = ""
            if state in _VALID_MSG_STATES:
                normalized = {"state": state, "at": at if isinstance(at, str) else ""}
                if isinstance(entry, dict):
                    if "pid" in entry:
                        normalized["pid"] = entry["pid"]
                    if "epoch" in entry:
                        normalized["epoch"] = entry["epoch"]
                    if isinstance(entry.get("task"), str):
                        normalized["task"] = entry["task"]
                messages[mid] = normalized
        return _state_with_pending(data, messages)

    return {"version": 2, "messages": {}}


def _load_state(project):
    try:
        data = read_json(_execute_state_path(project), default={}) or {}
    except RuntimeError:
        data = {}
    return _normalize_state(data)


def _prune_state(state):
    messages = state.setdefault("messages", {})
    while len(messages) > _STATE_LIMIT:
        messages.pop(next(iter(messages)))
    state["version"] = 2
    return state


def _write_state(project, state):
    atomic_write_json(_execute_state_path(project), _prune_state(state))


def _pending_greenlight(project):
    pending = _load_state(project).get("pending_greenlight")
    if not isinstance(pending, dict):
        return None
    epoch = pending.get("epoch")
    try:
        if epoch is not None and time.time() - float(epoch) > _GREENLIGHT_STALE_SECONDS:
            return None
    except (TypeError, ValueError):
        return None
    task = pending.get("task")
    if not isinstance(task, str) or not task.strip():
        return None
    return pending


def _set_pending_greenlight(project, task):
    data = _load_state(project)
    data["pending_greenlight"] = {
        "task": task,
        "id": _task_id(task),
        "at": now_str(),
        "epoch": time.time(),
    }
    _write_state(project, data)


def _clear_pending_greenlight(project, task=None):
    data = _load_state(project)
    pending = data.get("pending_greenlight")
    if not isinstance(pending, dict):
        return
    if task is not None and pending.get("id") != _task_id(task):
        return
    data.pop("pending_greenlight", None)
    _write_state(project, data)


def _release_claim(project, mid):
    data = _load_state(project)
    messages = data.setdefault("messages", {})
    entry = messages.get(mid) or {}
    if entry.get("state") == "claimed":
        messages.pop(mid, None)
        _write_state(project, data)


def _set_state(project, mid, state, task=None):
    data = _load_state(project)
    entry = {"state": state, "at": now_str()}
    if state == "claimed":
        entry.update({"pid": os.getpid(), "epoch": time.time()})
    if task:
        entry["task"] = task
    data.setdefault("messages", {})[mid] = entry
    _write_state(project, data)


def _msg_state(project, mid):
    return (_load_state(project).get("messages", {}).get(mid) or {}).get("state")


def _load_handled(project):
    data = _load_state(project)
    return [mid for mid, entry in data.get("messages", {}).items()
            if entry.get("state") == "done"]


def _mark_handled(project, mid):
    _set_state(project, mid, "done")


def _msg_key(msg):
    if msg.get("_id"):
        return msg["_id"]
    try:
        dup = int(msg.get("_dup", 0) or 0)
    except (TypeError, ValueError):
        dup = 0
    raw = "%s|%s|%s|%d" % (
        msg.get("ts", ""), msg.get("speaker", ""), msg.get("text", ""), dup)
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _claim_is_stale(entry):
    pid = entry.get("pid")
    epoch = entry.get("epoch")
    if not pid or epoch is None:
        return True
    try:
        owner_pid = int(pid)
        if owner_pid <= 0:
            return True
        if _pid_alive(owner_pid):
            age = time.time() - float(epoch)
            return age > _CLAIM_STALE_SECONDS
        return True
    except (TypeError, ValueError):
        return True


def _claim_next_message(project, msgs):
    run_id = "chat-execute-%s-%s" % (os.getpid(), uuid.uuid4().hex)
    lock_path = _execute_lock_path(project)
    if not acquire_lock(lock_path, run_id, ttl=30, wait=3):
        return "busy", None, None, []
    try:
        state = _load_state(project)
        messages = state.setdefault("messages", {})
        warnings = []
        changed = False
        for idx, msg in enumerate(msgs):
            if not _chat_roles.is_human(msg.get("speaker", ""), project):
                continue
            mid = _msg_key(msg)
            entry = messages.get(mid) or {}
            cur = entry.get("state")
            if cur is None or (cur == "claimed" and _claim_is_stale(entry)):
                messages[mid] = {
                    "state": "claimed",
                    "at": now_str(),
                    "pid": os.getpid(),
                    "epoch": time.time(),
                }
                _write_state(project, state)
                return "claimed", idx, msg, warnings
            if cur == "claimed":
                continue
            if cur == "running":
                warnings.append(
                    "⚠️ 上一个任务执行中断,需要你确认是否已完成/是否重跑:%s"
                    % msg.get("text", ""))
                _upsert_task_item(project, entry.get("task") or msg.get("text", ""),
                                  "failed", {"summary": "执行中断,需要确认是否已完成/是否重跑"})
                messages[mid] = {"state": "done", "at": now_str()}
                changed = True
        if changed:
            _write_state(project, state)
        return "none", None, None, warnings
    finally:
        release_lock(lock_path, run_id)


def report(project, task, result, poster):
    ok = bool(result.get("ok"))
    summary = result.get("summary") or ""
    commit = result.get("commit") or ""
    if ok:
        line = "✅ 完成:%s%s" % (task, ("(commit %s)" % commit) if commit else "")
    else:
        line = "❌ 失败:%s — %s" % (task, summary)
    poster(line)

    row = "- [%s] %s — %s%s%s\n" % (
        now_str()[:16], task, ("成功" if ok else "失败"),
        (" · " + summary) if summary else "",
        (" · commit " + commit) if commit else "")

    def _edit(body):
        body = _upsert_task_item_in_body(
            body, task, "done" if ok else "failed", result=result)
        if "## 执行记录" not in body:
            body = body.rstrip() + "\n\n## 执行记录\n" if body else "## 执行记录\n"
        return body.rstrip() + "\n" + row

    _update_issues(project, _edit)


def _parse_chat_fn():
    # bridge-chat-web.py is hyphen-named; load parse_chat the same way _chat_respond does
    here = os.path.dirname(os.path.abspath(__file__))
    spec = _ilu.spec_from_file_location("_cw", os.path.join(here, "bridge-chat-web.py"))
    mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.parse_chat


def _format_chat_fn():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = _ilu.spec_from_file_location("_cw", os.path.join(here, "bridge-chat-web.py"))
    mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.format_chat_message


def _stub_executor(task, project=None, image_path=None):
    return {"ok": False, "summary": "executor not wired (see next plan)"}


def _poster_speaker(project):
    return _chat_roles.lead_name(project) or "Lead"     # never empty


_default_executor = _chat_executor.run_task_executor


def execute_once(project, judge=None, executor=None, poster=None):
    execute_enabled = os.environ.get("BRIDGE_CHAT_EXECUTE") == "1"
    executor = executor or _default_executor
    if judge is None:
        return "none"
    from bridge_common import read_section
    msgs = _parse_chat_fn()(read_section(
        collab_paths(find_project_root(project))["board"], "Chat"))
    if not msgs:
        return "empty"
    if poster is None:
        lead = _poster_speaker(project)
        fmt = _format_chat_fn()
        poster = lambda text: _board_post(project, lead, fmt(lead, text), section="Chat")

    claim, selected_idx, selected, warnings = _claim_next_message(project, msgs)
    if claim == "busy":
        return "busy"
    for warning in warnings:
        poster(warning)
    if selected is None:
        return "none"

    mid = _msg_key(selected)
    decision = decide(project, msgs[:selected_idx + 1], judge)

    if not execute_enabled:
        try:
            st = _capture_only(project, decision, poster)
        except RuntimeError as exc:
            if str(exc) == "ISSUES.md lock busy":
                _release_claim(project, mid)
                return "retry"
            raise
        _set_state(project, mid, "done")
        return st

    captured = {}
    def _enqueue(task):
        captured["task"] = task
    try:
        st = dispatch(project, decision, poster, _enqueue)
    except RuntimeError as exc:
        if str(exc) == "ISSUES.md lock busy":
            _release_claim(project, mid)
            return "retry"
        raise
    if st != "acked-enqueued":
        _set_state(project, mid, "done")
        return {"asked": "asked", "requested-greenlight": "requested-greenlight",
                "recorded": "recorded", "noop": "ignore"}.get(st, "none")
    _upsert_task_item(project, captured["task"], "running")
    _set_state(project, mid, "running", task=captured["task"])
    _clear_pending_greenlight(project, captured["task"])
    result = executor(captured["task"], project, decision.get("image_path"))
    report(project, captured["task"], result, poster)
    _set_state(project, mid, "done")
    return "done"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    o = sub.add_parser("once")
    o.add_argument("--project", default=None)
    a = ap.parse_args()
    if a.cmd == "once":
        print(execute_once(a.project, judge=_chat_judge.default_judge))
    return 0


if __name__ == "__main__":
    sys.exit(main())
