"""Branch push + GitHub PR helpers for the AI PR gate.

All git and gh boundaries are injectable. The central invariant is exact-head
verification: local HEAD and the pushed remote branch head must both match the
signed evidence head before a PR is opened.
"""
import os
import re
import subprocess
import tempfile

import _pr_evidence


def _run(run, cmd, **kwargs):
    return run(cmd, **kwargs)


def _check(run, cmd, **kwargs):
    r = _run(run, cmd, **kwargs)
    if getattr(r, "returncode", 0) != 0:
        raise RuntimeError((getattr(r, "stderr", "") or "command failed").strip())
    return r


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "task"


def create_branch(repo, base, slug, run=subprocess.run):
    status = _check(
        run, ["git", "-C", repo, "status", "--porcelain"],
        capture_output=True, text=True)
    if status.stdout.strip():
        raise RuntimeError("worktree must be clean before creating an AI PR branch")
    base_sha = _check(
        run, ["git", "-C", repo, "rev-parse", "--verify", "%s^{commit}" % base],
        capture_output=True, text=True).stdout.strip()
    branch = "ai/%s-%s" % (_slug(slug), base_sha[:7])
    _check(
        run, ["git", "-C", repo, "checkout", "-b", branch, base],
        capture_output=True, text=True)
    return branch


def branch_patch_ids(repo, base, head, run=subprocess.run):
    revs = _check(
        run, ["git", "-C", repo, "rev-list", "%s..%s" % (base, head)],
        capture_output=True, text=True).stdout.splitlines()
    patch_ids = []
    for commit in revs:
        commit = commit.strip()
        if not commit:
            continue
        show = _check(
            run, ["git", "-C", repo, "show", commit],
            capture_output=True, text=True)
        patch = _check(
            run, ["git", "-C", repo, "patch-id", "--stable"],
            input=show.stdout, capture_output=True, text=True)
        line = (patch.stdout or "").strip().splitlines()
        if line:
            patch_ids.append(line[0].split()[0])
    return sorted(set(patch_ids))


def remote_head(repo, branch, run=subprocess.run):
    r = _run(
        run, ["git", "-C", repo, "ls-remote", "origin", branch],
        capture_output=True, text=True)
    if getattr(r, "returncode", 0) != 0:
        return None
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return None
    return line[0].split()[0]


def normalize_base_ref(base_ref):
    ref = (base_ref or "").strip()
    prefixes = ("refs/heads/", "refs/remotes/origin/", "origin/")
    for prefix in prefixes:
        if ref.startswith(prefix):
            return ref[len(prefix):]
    if ref.startswith("refs/remotes/"):
        parts = ref.split("/", 3)
        if len(parts) == 4:
            return parts[3]
    return ref


def _pr_base(evidence):
    return normalize_base_ref(
        evidence.get("base_ref") or evidence.get("base_branch")
        or evidence.get("base") or evidence.get("base_sha", ""))


def _test_result_word(evidence):
    return "PASSED" if evidence.get("tests_ok") is True else "FAILED"


def pr_body(evidence, sig):
    result = _test_result_word(evidence)
    canonical = _pr_evidence.canonical(evidence)
    head = evidence.get("head_sha", "")
    base = evidence.get("base_sha", "")
    patch_ids = evidence.get("patch_ids") or []
    return "\n".join([
        "AI-authored change - claude-codex-bridge review gate",
        "",
        "Implemented by: %s   Reviewed by: %s - VERDICT: %s" % (
            evidence.get("implementer", ""),
            evidence.get("reviewer", ""), evidence.get("verdict", "")),
        "Tamper-evident: this approval is SSHSIG-signed and bound to the exact diff "
        "(head %s, base %s, patch-ids %s) and the test run (%s). "
        "Any change after review invalidates it." % (
            head[:7], base[:7], ",".join(patch_ids) or "none", evidence.get("test_cmd", "")),
        "Tests: %s (%s)   Audit: evidence nonce %s" % (
            result, evidence.get("test_cmd", ""), evidence.get("nonce", "")),
        "Independence note: on a single host the signature attests key-holder "
        "accountability + exact-diff binding, not machine-independent identity. "
        "Human reviewer makes the final merge.",
        "",
        "Task hash: %s" % evidence.get("task_hash", ""),
        "Test log hash: %s" % evidence.get("test_log_hash", ""),
        "Branch: %s" % evidence.get("branch", ""),
        "Base: %s (%s)" % (_pr_base(evidence), evidence.get("base_sha", "")),
        "Head: %s" % evidence.get("head_sha", ""),
        "",
        "Signed evidence envelope:",
        "```json",
        canonical,
        "```",
        "",
        "SSHSIG signature:",
        "```",
        sig or "",
        "```",
        "",
    ])


def open_pr(repo, branch, evidence, sig, project, *, run=subprocess.run, gh=subprocess.run):
    if not _pr_evidence.verify(evidence, sig, project):
        raise RuntimeError("signed evidence did not verify")
    if (evidence.get("verdict") or "").upper() not in ("GO", "SHIP"):
        raise RuntimeError("signed evidence does not contain an approving reviewer verdict")
    if evidence.get("branch") != branch:
        raise RuntimeError("signed evidence branch does not match requested branch")
    if not evidence.get("tests_ok"):
        raise RuntimeError("signed evidence does not attest passing tests")

    local = _check(
        run, ["git", "-C", repo, "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    if not _pr_evidence.head_matches(evidence, local):
        raise RuntimeError("head moved since review: local HEAD does not match signed evidence")

    _check(
        run, ["git", "-C", repo, "push", "origin", branch],
        capture_output=True, text=True)
    pushed = remote_head(repo, branch, run=run)
    if not _pr_evidence.head_matches(evidence, pushed):
        raise RuntimeError("remote head differs from signed evidence")

    fd, path = tempfile.mkstemp(prefix="claude-codex-pr-", suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pr_body(evidence, sig))
        title = "AI PR Gate: %s" % branch
        r = gh(
            ["gh", "pr", "create", "--head", branch,
             "--base", _pr_base(evidence),
             "--title", title, "--body-file", path],
            capture_output=True, text=True)
        if getattr(r, "returncode", 0) != 0:
            raise RuntimeError((getattr(r, "stderr", "") or "gh pr create failed").strip())
        return (getattr(r, "stdout", "") or "").strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
