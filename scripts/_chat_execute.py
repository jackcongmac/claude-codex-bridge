"""Chat-driven execution — P0a deterministic core. Turns a human chat message into a
gated, reported execution decision. LLM judgment and the write-capable executor are
injected boundaries (see execute_once). Safe: gated behind BRIDGE_CHAT_EXECUTE=1."""
import argparse
import importlib.util as _ilu
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, now_str  # noqa: E402
import _chat_roles  # noqa: E402
from _post import post as _board_post   # noqa: E402
import _chat_executor  # noqa: E402
import _chat_judge  # noqa: E402

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


def _parse_chat_fn():
    # bridge-chat-web.py is hyphen-named; load parse_chat the same way _chat_respond does
    here = os.path.dirname(os.path.abspath(__file__))
    spec = _ilu.spec_from_file_location("_cw", os.path.join(here, "bridge-chat-web.py"))
    mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.parse_chat


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
    if poster is None:
        lead = _poster_speaker(project)
        poster = lambda text: _board_post(project, lead, text, section="Chat")
    decision = decide(project, msgs, judge)

    captured = {}
    def _enqueue(task):
        captured["task"] = task
    st = dispatch(project, decision, poster, _enqueue)
    if st != "acked-enqueued":
        return {"asked": "asked", "requested-greenlight": "requested-greenlight",
                "noop": "ignore"}.get(st, "none")
    result = executor(captured["task"], project)
    report(project, captured["task"], result, poster)
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
