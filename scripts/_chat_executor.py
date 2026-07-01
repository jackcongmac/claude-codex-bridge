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
import _agent_cli

_DEFAULT_AGENTS = ("Codex", "Claude")   # neutral fallback when roster unknown


def run_task(project, task, implement, review, push, max_fix_rounds=2, image_path=None):
    findings = ""
    impl = implement(project, task, findings, image_path)
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
        impl = implement(project, task, findings, image_path)
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


def _remote_tracking_ref(root):
    """Return the current branch's upstream sha, or origin/<branch> if no upstream is set."""
    up = subprocess.run(
        ["git", "-C", root, "rev-parse", "--symbolic-full-name", "@{u}"],
        capture_output=True, text=True)
    name = up.stdout.strip()
    if not name:
        br = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        name = "refs/remotes/origin/%s" % br if br else ""
    if not name:
        return ""
    rr = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", "-q", name],
        capture_output=True, text=True)
    return rr.stdout.strip()


def _roles_for(project):
    """Return (implementer, reviewer). Reviewer = configured lead; implementer = the other
    known agent. No identity hardcoded beyond a neutral two-agent fallback."""
    roles = _chat_roles.load_roles(project)
    reviewer = roles["lead"] or _DEFAULT_AGENTS[1]
    implementer = next((a for a in _DEFAULT_AGENTS if a != reviewer), _DEFAULT_AGENTS[0])
    return implementer, reviewer


def default_implement(project, task, findings, image_path=None):
    root = find_project_root(project)
    implementer, _reviewer = _roles_for(root)
    base = _git_head(root)
    remote_before = _remote_tracking_ref(root)
    prompt = (
        "Implement this task with TDD (write a failing test, make it pass), commit your work, "
        "and DO NOT push. Task: %s" % task
        + (("\n\nReviewer findings to address:\n%s" % findings) if findings else ""))
    cmd = _agent_cli.implement_argv(implementer, prompt, root, image_path=image_path)
    subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800)
    remote_after = _remote_tracking_ref(root)
    head = _git_head(root)
    if remote_before and remote_after and remote_before != remote_after:
        return {"ok": False, "head_sha": head,
                "test_summary": "SECURITY: implementer moved the remote ref during implement — aborting, no push"}
    return {"ok": head != base, "head_sha": head,
            "test_summary": "see implementer output"}


def _parse_verdict(text):
    """Extract the reviewer's FINAL 'VERDICT: GO|FIX-FIRST <reason>' line — the LAST one wins, so
    a verdict mentioned mid-reasoning doesn't count. Fail SAFE: no parseable verdict -> FIX-FIRST
    (never auto-approve on ambiguity)."""
    matches = re.findall(r"VERDICT:\s*(GO|FIX-FIRST)\b[ \t]*([^\n]*)", text or "", re.I)
    if not matches:
        return ("FIX-FIRST", "no parseable verdict from reviewer")
    kind, note = matches[-1]
    return (kind.upper(), note.strip())


def _review_call_llm(prompt, root, reviewer=None):
    if reviewer is None:
        _implementer, reviewer = _roles_for(root)
    return subprocess.run(_agent_cli.review_argv(reviewer, prompt, root), cwd=root,
                          capture_output=True, text=True, timeout=900).stdout or ""


def _run_tests(root):
    """Run the project's suite; return True iff green. The headless reviewer agent can't execute
    tests in its session, so the ORCHESTRATOR runs them and treats RED as a hard FIX-FIRST — failing
    tests can never be autonomously pushed regardless of what the LLM reviewer says."""
    try:
        r = subprocess.run(["python3", "-m", "unittest", "discover", "-s", "tests", "-q"],
                           cwd=root, capture_output=True, text=True, timeout=600)
        output = "%s\n%s" % (r.stdout or "", r.stderr or "")
        ran = re.search(r"\bRan\s+(\d+)\s+tests?\b", output)
        return (
            r.returncode == 0
            and ran is not None
            and int(ran.group(1)) >= 1
            and output.rstrip().endswith("OK")
        )
    except (OSError, subprocess.SubprocessError):
        return False


def default_review(project, head_sha, call_llm=None, run_tests=None):
    root = find_project_root(project)
    _implementer, reviewer = _roles_for(root)
    tests_ok = (run_tests or _run_tests)(root)
    prompt = (
        "You are %s, the code reviewer. Inspect the git commit at HEAD (%s) in this repo "
        "(run `git show %s`) for correctness, spec compliance, and code quality. The automated "
        "test suite was just run and it %s. Give brief reasoning, then end with EXACTLY one final "
        "line and nothing after it:\n"
        "VERDICT: GO <one-line reason>          (only if genuinely correct AND tests passed)\n"
        "VERDICT: FIX-FIRST <one-line reason>   (otherwise)"
        % (reviewer, head_sha, head_sha, ("PASSED" if tests_ok else "FAILED")))
    out = call_llm(prompt, root) if call_llm else _review_call_llm(prompt, root, reviewer)
    verdict, note = _parse_verdict(out)
    if not tests_ok:
        verdict = "FIX-FIRST"                                   # red tests never auto-GO
        note = ("automated tests FAILED — " + note).strip()
    # The reviewing agent IS the lead — same identity as this orchestrator — so recording the
    # verdict here is the lead recording its own review (recorded_by matches reviewer; not
    # cross-identity forgery). The bridge-push gate remains the independent backstop.
    from _review import record, latest_verdict
    record_status = record(root, reviewer, head_sha, verdict, note=note or "auto-review")
    if record_status != "ok":
        return {"verdict": "FIX-FIRST",
                "note": "could not record review: %s" % record_status}
    return latest_verdict(root, head_sha)


def default_push(project):
    root = find_project_root(project)
    implementer, reviewer = _roles_for(root)
    r = subprocess.run(["scripts/bridge-push.sh", implementer],
                       cwd=root, capture_output=True, text=True, timeout=300)
    return {"ok": r.returncode == 0, "pushed_sha": _git_head(root)}


def run_task_executor(task, project, image_path=None):
    return run_task(
        project, task, default_implement, default_review, default_push,
        image_path=image_path)
