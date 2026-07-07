#!/usr/bin/env python3
"""CLI orchestration for `claude-codex-bridge ship`.

P0 keeps this intentionally linear: create a clean branch, run the injected
implement/review/fix loop, sign exact evidence, verify exact heads, and open a PR.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import find_project_root  # noqa: E402
import _agent_cli  # noqa: E402
import _chat_executor  # noqa: E402
import _pr  # noqa: E402
import _pr_evidence  # noqa: E402
import bridge_route  # noqa: E402
import bridge_usage  # noqa: E402


def _git(root, *args, check=True):
    r = subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or "git command failed").strip())
    return r.stdout.strip()


def _default_base(root):
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                    check=False)
    return upstream or "main"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "task"


def _run_test_cmd(root, test_cmd):
    r = subprocess.run(
        test_cmd, cwd=root, shell=True, capture_output=True, text=True, timeout=1200)
    log = "%s%s" % (r.stdout or "", r.stderr or "")
    # P0 heuristic: a command that exercises a real suite should emit some log.
    # This blocks trivial pass-through commands like `true`; it is not proof of coverage.
    ok = (
        r.returncode == 0
        and bool(log.strip())
        and "FAILED" not in log
        and "Traceback (most recent call last)" not in log
    )
    return ok, log


def _git_head(root):
    return _git(root, "rev-parse", "HEAD")


def _changed_files(root, base, head):
    out = _git(root, "diff", "--name-only", "%s..%s" % (base, head), check=False)
    return [line for line in out.splitlines() if line.strip()]


def _diffstat(root, base, head):
    return _git(root, "diff", "--stat", "%s..%s" % (base, head), check=False)


def _print_dry_run_report(state, branch):
    evidence = state["evidence"] or {}
    plan = _pr.pr_plan(branch, evidence, state["sig"])
    print("status: DRY_RUN")
    print("DRY RUN — would open PR")
    print("evidence:")
    print(json.dumps(evidence, sort_keys=True, indent=2))
    print("verdict: %s" % evidence.get("verdict", ""))
    print("tests_ok: %s" % evidence.get("tests_ok", ""))
    print("pr_title: %s" % plan["title"])
    print("pr_base: %s" % plan["base"])
    print("pr_head: %s" % plan["head"])
    print("pr_body:")
    print(plan["body"])


def _other_actor(actor):
    if actor == "Claude":
        return "Codex"
    if actor == "Codex":
        return "Claude"
    return None


def resolve_roles(reco, cli_implementer, cli_reviewer, default_impl, default_rev):
    """Resolve ship implementer/reviewer from CLI, route advice, and static defaults."""
    reco = reco or {}
    notes = []
    if cli_implementer or cli_reviewer:
        implementer = cli_implementer or _other_actor(cli_reviewer) or default_impl
        reviewer = cli_reviewer or _other_actor(implementer) or default_rev
        source = "cli"
        reco_impl = reco.get("implementer")
        if (
            reco.get("confidence") == "high"
            and reco_impl
            and reco_impl != implementer
        ):
            notes.append(
                "quota suggests %s implements (%s), you specified %s"
                % (reco_impl, reco.get("reason", ""), implementer)
            )
    elif (
        reco.get("confidence") == "high"
        and reco.get("implementer")
        and reco.get("reviewer")
    ):
        implementer = reco["implementer"]
        reviewer = reco["reviewer"]
        source = "route"
    else:
        implementer = default_impl
        reviewer = default_rev
        source = "default"
        notes.append("route confidence %s -> using default roles" % reco.get("confidence"))
        notes.extend(reco.get("warnings") or [])
    if implementer == reviewer:
        raise ValueError("implementer and reviewer must be different actors")
    return {
        "implementer": implementer,
        "reviewer": reviewer,
        "source": source,
        "notes": notes,
    }


def _signal_pct(signals, model, key):
    value = (signals.get(model) or {}).get(key)
    if value is None:
        return "—"
    try:
        return "%d%%" % int(round(float(value)))
    except (TypeError, ValueError):
        return "—"


def route_banner_lines(reco, resolved):
    """Return human-readable advisory route banner lines without side effects."""
    reco = reco or {}
    resolved = resolved or {}
    signals = reco.get("signals") or {}
    lines = [
        "[route] quota: Claude 5h %s · 7d %s    Codex 5h %s · wk %s"
        % (
            _signal_pct(signals, "Claude", "five_h"),
            _signal_pct(signals, "Claude", "weekly"),
            _signal_pct(signals, "Codex", "five_h"),
            _signal_pct(signals, "Codex", "weekly"),
        ),
        "[route] recommend: implement->%s review->%s  [%s]  (%s)"
        % (
            reco.get("implementer") or "—",
            reco.get("reviewer") or "—",
            reco.get("confidence") or "—",
            reco.get("reason") or "—",
        ),
        "[route] using %s roles: implement->%s review->%s"
        % (
            resolved.get("source") or "—",
            resolved.get("implementer") or "—",
            resolved.get("reviewer") or "—",
        ),
    ]
    for note in resolved.get("notes") or []:
        lines.append("[route] note: %s" % note)
    for warning in reco.get("warnings") or []:
        lines.append("[route] WARN %s" % warning)
    return lines


def _make_callbacks(root, task, base_ref, base_sha, branch, test_cmd,
                    implementer, reviewer, dry_run=False):
    state = {
        "branch": branch,
        "verdict": "",
        "review_note": "",
        "test_log": "",
        "tests_ok": False,
        "pr_url": "",
        "evidence": None,
        "sig": None,
    }

    def implement(project, task_text, findings, image_path=None):
        before = _git_head(root)
        remote_before = _pr.remote_head(root, branch)
        prompt = (
            "Implement this task with TDD on branch %s. Commit your work and DO NOT push. "
            "The orchestrator will run this test command after you finish: %s\n\n"
            "Task:\n%s" % (branch, test_cmd, task_text)
        )
        if findings:
            prompt += "\n\nReviewer findings to address:\n%s" % findings
        cmd = _agent_cli.implement_argv(implementer, prompt, root, image_path=image_path)
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800)
        head = _git_head(root)
        remote_after = _pr.remote_head(root, branch)
        if remote_before != remote_after:
            return {"ok": False, "head_sha": head,
                    "test_summary": "SECURITY: implementer moved the remote branch during implement"}
        if r.returncode != 0:
            return {"ok": False, "head_sha": head,
                    "test_summary": "implementer failed"}
        if head == before:
            return {"ok": False, "head_sha": head,
                    "test_summary": "implementer produced no new commit"}
        tests_ok, test_log = _run_test_cmd(root, test_cmd)
        state["tests_ok"] = tests_ok
        state["test_log"] = test_log
        return {"ok": tests_ok, "head_sha": head,
                "test_summary": "tests PASSED" if tests_ok else "tests FAILED"}

    def review(project, head_sha):
        merge_base = _git(root, "merge-base", base_ref, "HEAD")
        files = _changed_files(root, merge_base, head_sha)
        stat = _diffstat(root, merge_base, head_sha)
        prompt = (
            "You are %s, the independent reviewer for an AI PR Gate run.\n"
            "Review ONLY the branch diff %s..%s for correctness, safety, and spec compliance.\n"
            "Task: %s\n"
            "Base SHA: %s\nHead SHA: %s\n"
            "Test command: %s\nTest result: %s\n"
            "Changed files:\n%s\n\nDiffstat:\n%s\n\n"
            "End with EXACTLY one final line and nothing after it:\n"
            "VERDICT: GO <one-line reason>\n"
            "VERDICT: FIX-FIRST <one-line reason>"
            % (reviewer, merge_base, head_sha, task, base_sha, head_sha, test_cmd,
               "PASSED" if state["tests_ok"] else "FAILED",
               "\n".join(files) or "(none)", stat or "(none)")
        )
        out = subprocess.run(
            _agent_cli.review_argv(reviewer, prompt, root),
            cwd=root, capture_output=True, text=True, timeout=900).stdout or ""
        verdict, note = _chat_executor._parse_verdict(out)
        if not state["tests_ok"]:
            verdict = "FIX-FIRST"
            note = ("tests FAILED - " + note).strip()
        state["verdict"] = verdict
        state["review_note"] = note
        return {"verdict": verdict, "note": note}

    def push(project):
        head = _git_head(root)
        if not state["tests_ok"]:
            return {"ok": False, "pushed_sha": head,
                    "summary": "cannot open PR without a passing non-empty test run"}
        if state["verdict"] not in ("GO", "SHIP"):
            return {"ok": False, "pushed_sha": head,
                    "summary": "cannot open PR without approving reviewer verdict"}
        patch_ids = _pr.branch_patch_ids(root, base_sha, head)
        evidence = _pr_evidence.build_evidence(
            task, base_sha, head, patch_ids, test_cmd, state["test_log"],
            state["tests_ok"], branch, implementer, reviewer, state["verdict"])
        evidence["base_ref"] = _pr.normalize_base_ref(base_ref)
        sig = _pr_evidence.sign(evidence, reviewer, root)
        if not sig:
            return {"ok": False, "pushed_sha": head,
                    "summary": "reviewer key missing; cannot sign evidence"}
        if not _pr_evidence.verify(evidence, sig, root):
            return {"ok": False, "pushed_sha": head,
                    "summary": "signed evidence did not verify"}
        try:
            pr_url = _pr.open_pr(root, branch, evidence, sig, root, dry_run=dry_run)
        except RuntimeError as exc:
            return {"ok": False, "pushed_sha": head, "summary": str(exc)}
        state["evidence"] = evidence
        state["sig"] = sig
        state["pr_url"] = pr_url
        return {"ok": True, "pushed_sha": head, "pr_url": pr_url}

    return state, implement, review, push


def run(args):
    root = find_project_root(args.project)
    task = " ".join(args.task).strip()
    if not task:
        raise RuntimeError("task is required")
    base_ref = args.base or _default_base(root)
    base_sha = _git(root, "rev-parse", "--verify", "%s^{commit}" % base_ref)
    if not getattr(args, "no_route", False):
        reco = bridge_route.recommend(
            bridge_usage.read_claude(),
            bridge_usage.read_codex(),
            now_ts=time.time(),
        )
    else:
        reco = {
            "implementer": None,
            "reviewer": None,
            "confidence": "low",
            "reason": "route disabled (--no-route)",
            "warnings": [],
            "signals": {},
        }
    default_impl, default_rev = _chat_executor._roles_for(root)
    resolved = resolve_roles(
        reco, args.implementer, args.reviewer, default_impl, default_rev)
    implementer, reviewer = resolved["implementer"], resolved["reviewer"]
    for line in route_banner_lines(reco, resolved):
        print(line, file=sys.stderr)

    branch = _pr.create_branch(root, base_ref, _slug(task))
    state, implement, review, push = _make_callbacks(
        root, task, base_ref, base_sha, branch, args.test_cmd, implementer, reviewer,
        dry_run=args.dry_run)
    result = _chat_executor.run_task(
        root, task, implement=implement, review=review, push=push,
        max_fix_rounds=args.max_fix_rounds)
    if not result.get("ok"):
        print("status: FAILED")
        print("branch: %s" % branch)
        print("summary: %s" % result.get("summary", ""))
        return 1
    if args.dry_run:
        _print_dry_run_report(state, branch)
        return 0
    evidence = state["evidence"] or {}
    print("status: OPENED_PR")
    print("branch: %s" % branch)
    print("pr_url: %s" % state["pr_url"])
    print("verdict: %s" % state["verdict"])
    print("test_result: PASSED")
    print("evidence_nonce: %s" % evidence.get("nonce", ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="claude-codex-bridge ship",
        description="AI PR Gate: create a signed, reviewed PR from one task.")
    ap.add_argument("--test-cmd", required=True,
                    help="explicit test command required for P0")
    ap.add_argument("--base", default=None,
                    help="base ref to branch from; defaults to current upstream or main")
    ap.add_argument("--project", default=".",
                    help="project path; defaults to current directory")
    ap.add_argument("--implementer", default=None,
                    help="actor that implements the change")
    ap.add_argument("--reviewer", default=None,
                    help="actor that reviews and signs the evidence")
    ap.add_argument("--no-route", action="store_true",
                    help="skip quota route recommendation and use static default roles")
    ap.add_argument("--max-fix-rounds", type=int, default=2,
                    help="maximum implement/fix attempts after FIX-FIRST")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the full PR gate but do not push or create a GitHub PR")
    ap.add_argument("task", nargs="+", help="task description")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        print("status: FAILED")
        print("summary: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
