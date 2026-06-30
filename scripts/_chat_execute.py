"""Chat-driven execution — P0a deterministic core. Turns a human chat message into a
gated, reported execution decision. LLM judgment and the write-capable executor are
injected boundaries (see execute_once). Safe: gated behind BRIDGE_CHAT_EXECUTE=1."""
import argparse
import hashlib
import importlib.util as _ilu
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    acquire_lock,
    atomic_write_json,
    collab_paths,
    find_project_root,
    now_str,
    read_json,
    release_lock,
)
import _chat_roles  # noqa: E402
from _post import post as _board_post   # noqa: E402
import _chat_executor  # noqa: E402
import _chat_judge  # noqa: E402

_HIGH_RISK = re.compile(
    r"(发版|发布|publish|release|打?\s*tag\b|删\s*(除|文件|掉)|\bdelete\b|\brm\b|"
    r"force[-\s]?push|--force|git\s+reset\s+--hard\b|reset\s+--hard\b|"
    r"git\s+clean\b|clean\s+-fdx\b|(?<![-\w])drop(?![-\w])|\bwipe\b|\berase\b|"
    r"抹除|清空|清除|\bdeploy\b|部署|\bprod(?:uction)?\b|线上|生产|"
    r"\bsecret\b|token\s+rotation|轮换密钥|\brotate\b)", re.I)


def is_high_risk(task_text):
    return bool(_HIGH_RISK.search(task_text or ""))


def decide(project, msgs, judge):
    if not msgs:
        return {"action": "none"}
    latest = msgs[-1]
    if not _chat_roles.is_human(latest.get("speaker", ""), project):
        return {"action": "ignore", "reason": "not-human"}
    verdict = judge(latest.get("text", ""), msgs[:-1]) or {}
    kind = verdict.get("kind")
    if kind == "actionable":
        task = (verdict.get("task") or latest.get("text", "")).strip()
        if is_high_risk(task) or is_high_risk(latest.get("text", "")):
            return {"action": "request_greenlight", "task": task}
        return {"action": "execute", "task": task}
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
        poster("这个需要你点头才能做(高风险):%s" % decision.get("task", ""))
        return "requested-greenlight"
    if action == "ask":
        poster(decision.get("question") or "你是要现在就开始做吗?")
        return "asked"
    return "noop"


def _issues_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"], "ISSUES.md")


def _execute_state_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"],
                        "chat_execute_state.json")


def _execute_lock_path(project):
    return os.path.join(collab_paths(find_project_root(project))["dir"],
                        "chat_execute_state.lock")


_STATE_LIMIT = 500
_VALID_MSG_STATES = {"claimed", "running", "done"}


def _normalize_state(data):
    messages = {}
    if isinstance(data, dict) and isinstance(data.get("handled"), list):
        for mid in data.get("handled", []):
            if isinstance(mid, str) and mid not in messages:
                messages[mid] = {"state": "done", "at": ""}
        return {"version": 2, "messages": messages}

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
                messages[mid] = {"state": state, "at": at if isinstance(at, str) else ""}
        return {"version": 2, "messages": messages}

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


def _set_state(project, mid, state):
    data = _load_state(project)
    data.setdefault("messages", {})[mid] = {"state": state, "at": now_str()}
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
            cur = (messages.get(mid) or {}).get("state")
            if cur in (None, "claimed"):
                messages[mid] = {"state": "claimed", "at": now_str()}
                _write_state(project, state)
                return "claimed", idx, msg, warnings
            if cur == "running":
                warnings.append(
                    "⚠️ 上一个任务执行中断,需要你确认是否已完成/是否重跑:%s"
                    % msg.get("text", ""))
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

    path = _issues_path(project)
    try:
        with open(path) as f:
            body = f.read()
    except OSError:
        body = ""
    if "## 执行记录" not in body:
        body = body.rstrip() + "\n\n## 执行记录\n" if body else "## 执行记录\n"
    row = "- [%s] %s — %s%s%s\n" % (
        now_str()[:16], task, ("成功" if ok else "失败"),
        (" · " + summary) if summary else "",
        (" · commit " + commit) if commit else "")
    with open(path, "w") as f:
        f.write(body.rstrip() + "\n" + row)


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


def _stub_executor(task):
    return {"ok": False, "summary": "executor not wired (see next plan)"}


def _poster_speaker(project):
    return _chat_roles.lead_name(project) or "Lead"     # never empty


_default_executor = _chat_executor.run_task_executor


def execute_once(project, judge=None, executor=None, poster=None):
    if os.environ.get("BRIDGE_CHAT_EXECUTE") != "1":
        return "disabled"
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
    if decision.get("action") != "execute":
        _set_state(project, mid, "done")

    captured = {}
    def _enqueue(task):
        captured["task"] = task
    st = dispatch(project, decision, poster, _enqueue)
    if st != "acked-enqueued":
        return {"asked": "asked", "requested-greenlight": "requested-greenlight",
                "noop": "ignore"}.get(st, "none")
    _set_state(project, mid, "running")
    result = executor(captured["task"], project)
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
