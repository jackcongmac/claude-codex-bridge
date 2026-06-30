"""Real executor pipeline for chat-driven execution: implement -> review -> (fix loop)
-> push, for ONE greenlit task. The three steps are injected callables (defaults shell
out to codex exec / a headless reviewer / bridge-push.sh). The bridge-push review gate is
the hard safety backstop — unreviewed code cannot reach origin even if this misbehaves."""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import find_project_root
import _chat_roles

_DEFAULT_AGENTS = ("Codex", "Claude")   # neutral fallback when roster unknown


def run_task(project, task, implement, review, push, max_fix_rounds=2):
    findings = ""
    impl = implement(project, task, findings)
    if not impl.get("ok"):
        return {"ok": False, "commit": "",
                "summary": "实现失败:%s" % impl.get("test_summary", "")}
    head = impl.get("head_sha", "")

    rounds = 0
    while True:
        verdict = review(project, head)
        if verdict.get("verdict") == "GO":
            break
        rounds += 1
        if rounds >= max_fix_rounds:
            return {"ok": False, "commit": head,
                    "summary": "互审未通过(%d 轮):%s" % (rounds, verdict.get("note", ""))}
        findings = verdict.get("note", "")
        impl = implement(project, task, findings)
        if not impl.get("ok"):
            return {"ok": False, "commit": head,
                    "summary": "修复后实现失败:%s" % impl.get("test_summary", "")}
        head = impl.get("head_sha", head)

    pushed = push(project)
    if not pushed.get("ok"):
        return {"ok": False, "commit": head, "summary": "push 失败"}
    return {"ok": True, "commit": pushed.get("pushed_sha", head), "summary": "完成并已推送"}


def _git_head(project):
    return subprocess.run(
        ["git", "-C", project, "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()


def _roles_for(project):
    """Return (implementer, reviewer). Reviewer = configured lead; implementer = the other
    known agent. No identity hardcoded beyond a neutral two-agent fallback."""
    roles = _chat_roles.load_roles(project)
    reviewer = roles["lead"] or _DEFAULT_AGENTS[1]
    implementer = next((a for a in _DEFAULT_AGENTS if a != reviewer), _DEFAULT_AGENTS[0])
    return implementer, reviewer


def default_implement(project, task, findings):
    root = find_project_root(project)
    base = _git_head(root)
    prompt = (
        "Implement this task with TDD (write a failing test, make it pass), commit your work, "
        "and DO NOT push. Task: %s" % task
        + (("\n\nReviewer findings to address:\n%s" % findings) if findings else ""))
    subprocess.run(["codex", "exec", "-s", "danger-full-access", prompt],
                   cwd=root, capture_output=True, text=True, timeout=1800)
    head = _git_head(root)
    return {"ok": head != base, "head_sha": head,
            "test_summary": "see codex output"}


def _parse_verdict(text):
    """Extract the reviewer's FINAL 'VERDICT: GO|FIX-FIRST <reason>' line — the LAST one wins, so
    a verdict mentioned mid-reasoning doesn't count. Fail SAFE: no parseable verdict -> FIX-FIRST
    (never auto-approve on ambiguity)."""
    matches = re.findall(r"VERDICT:\s*(GO|FIX-FIRST)\b[ \t]*([^\n]*)", text or "", re.I)
    if not matches:
        return ("FIX-FIRST", "no parseable verdict from reviewer")
    kind, note = matches[-1]
    return (kind.upper(), note.strip())


def _review_call_llm(prompt, root):
    return subprocess.run(["claude", "-p", prompt], cwd=root,
                          capture_output=True, text=True, timeout=900).stdout or ""


def default_review(project, head_sha, call_llm=None):
    root = find_project_root(project)
    _implementer, reviewer = _roles_for(root)
    prompt = (
        "You are %s, the code reviewer. Inspect the git commit at HEAD (%s) in this repo "
        "(run `git show %s` and run the relevant tests) for correctness, spec compliance, code "
        "quality, and that its tests pass. Give brief reasoning, then end with EXACTLY one final "
        "line and nothing after it:\n"
        "VERDICT: GO <one-line reason>          (only if genuinely correct and tests pass)\n"
        "VERDICT: FIX-FIRST <one-line reason>   (otherwise)"
        % (reviewer, head_sha, head_sha))
    out = (call_llm or _review_call_llm)(prompt, root)
    verdict, note = _parse_verdict(out)
    # The reviewing agent IS the lead — the same identity as this orchestrator — so recording
    # the verdict here is the lead recording its own review (recorded_by matches reviewer; not
    # cross-identity forgery). The bridge-push gate remains the independent backstop.
    from _review import record, latest_verdict
    record(root, reviewer, head_sha, verdict, note=note or "auto-review")
    return latest_verdict(root, head_sha)


def default_push(project):
    root = find_project_root(project)
    implementer, reviewer = _roles_for(root)
    r = subprocess.run(["scripts/bridge-push.sh", implementer],
                       cwd=root, capture_output=True, text=True, timeout=300)
    return {"ok": r.returncode == 0, "pushed_sha": _git_head(root)}


def run_task_executor(task, project):
    return run_task(project, task, default_implement, default_review, default_push)
