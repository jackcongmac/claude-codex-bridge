"""Chat-driven execution — P0a deterministic core. Turns a human chat message into a
gated, reported execution decision. LLM judgment and the write-capable executor are
injected boundaries (see execute_once). Safe: gated behind BRIDGE_CHAT_EXECUTE=1."""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, now_str  # noqa: E402
import _chat_roles  # noqa: E402

_HIGH_RISK = re.compile(
    r"(发版|发布|publish|release|打?\s*tag\b|删\s*(除|文件|掉)|\bdelete\b|\brm\b|"
    r"force[-\s]?push|--force|\bdrop\b)", re.I)


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
        if is_high_risk(task):
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
