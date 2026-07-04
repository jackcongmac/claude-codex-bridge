"""Signed PR evidence envelopes for the AI PR gate.

The envelope intentionally stores hashes for task text and test logs. It binds the
review signature to the exact base/head, patch-id set, test command, and reviewer
verdict without copying potentially sensitive logs into PR text.
"""
import hashlib
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sig  # noqa: E402


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_evidence(task, base_sha, head_sha, patch_ids, test_cmd, test_log,
                   tests_ok, branch, implementer, reviewer, verdict):
    return {
        "task_hash": sha256_text(task),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "patch_ids": sorted(set(patch_ids or [])),
        "test_cmd": test_cmd,
        "test_log_hash": sha256_text(test_log),
        "tests_ok": bool(tests_ok),
        "branch": branch,
        "implementer": implementer,
        "reviewer": reviewer,
        "verdict": (verdict or "").upper(),
        "nonce": secrets.token_hex(16),
    }


def canonical(evidence):
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"))


def sign(evidence, reviewer, project):
    return _sig.sign(reviewer, canonical(evidence), project=project)


def verify(evidence, sig, project):
    reviewer = evidence.get("reviewer") if isinstance(evidence, dict) else None
    implementer = evidence.get("implementer") if isinstance(evidence, dict) else None
    if not (reviewer or "").strip():
        return False
    if not (implementer or "").strip():
        return False
    if (implementer or "").strip().lower() == (reviewer or "").strip().lower():
        return False
    return _sig.verify(reviewer, canonical(evidence), sig, project=project)


def head_matches(evidence, actual_head_sha):
    return bool(evidence and evidence.get("head_sha") == actual_head_sha)
