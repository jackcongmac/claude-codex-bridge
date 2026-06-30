"""Real executor pipeline for chat-driven execution: implement -> review -> (fix loop)
-> push, for ONE greenlit task. The three steps are injected callables (defaults shell
out to codex exec / a headless reviewer / bridge-push.sh). The bridge-push review gate is
the hard safety backstop — unreviewed code cannot reach origin even if this misbehaves."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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
