#!/usr/bin/env python3
"""_review.py — the review ledger + approval check behind the gated push.

Mechanizes "review before merge" (non-negotiable #4): a reviewer records a verdict
bound to a specific commit SHA, and bridge-push refuses to push a SHA that no PEER
has approved. This converts a verbal "the peer reviewed it" claim into an auditable
artifact + a hard gate — exactly the gap that let an agent push unreviewed work and
attribute a review that never happened.

Honest boundary: new ledger entries record `recorded_by` from the local agent
environment, and the gate rejects approving entries with an explicit
`recorded_by != reviewer` mismatch. Legacy entries without `recorded_by` still
count for compatibility. This blocks the common "Codex records as Claude" footgun
when the environment identifies Codex, but it is still not cryptographic identity
binding — see DESIGN/roadmap.

CLI:
  _review.py record --self R --sha S --verdict V [--target T] [--note N] [--bypass]
  _review.py check  --sha S --exclude ACTOR [--json]   # exit 0 = approved, 1 = not
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, read_json, atomic_write_json, now_str,
    acquire_lock, release_lock,
)

APPROVING = {"SHIP", "GO"}


def _nonce():
    return "%x-%x" % (int(time.time() * 1000), os.getpid())


def _detected_recorder(self_name, env=None):
    env = env or os.environ
    if env.get("CODEX_CI") or env.get("CODEX_THREAD_ID"):
        return "Codex"
    if (env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT")
            or env.get("CLAUDE_CODE") or env.get("CLAUDE_SESSION_ID")):
        return "Claude"
    return self_name


def _canonical_sha(project, sha):
    """Return git's full commit id for a rev when possible; otherwise keep it raw."""
    try:
        resolved = subprocess.run(
            ["git", "-C", project, "rev-parse", "--verify", "--quiet",
             "--end-of-options", "%s^{commit}" % sha],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return sha
    if resolved.returncode != 0:
        return sha
    full_sha = resolved.stdout.strip()
    return full_sha or sha


def record(project, reviewer, sha, verdict, target=None, note=None, bypass=False,
           wait=10.0):
    """Append a review entry under the collaboration lock.

    Returns: "ok", "actor_mismatch", or "lockbusy".
    """
    recorded_by = _detected_recorder(reviewer)
    if recorded_by != reviewer:
        return "actor_mismatch"
    p = collab_paths(project)
    entry = {"reviewer": reviewer, "sha": _canonical_sha(project, sha),
             "verdict": (verdict or "").upper(),
             "target": target, "note": note, "bypass": bool(bypass),
             "recorded_by": recorded_by, "ts": now_str(), "nonce": _nonce()}
    if not acquire_lock(p["lock"], "review-%s" % reviewer, ttl=30, wait=wait):
        return "lockbusy"
    try:
        led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
        led.setdefault("reviews", []).append(entry)
        atomic_write_json(p["reviews"], led)
        return "ok"
    finally:
        release_lock(p["lock"], "review-%s" % reviewer)


def has_approval(project, sha, exclude_actor):
    """True iff a NON-bypass approving (SHIP/GO) verdict exists for `sha` by a
    reviewer other than `exclude_actor` (you can't approve your own push)."""
    p = collab_paths(project)
    led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
    for e in led.get("reviews", []):
        recorded_by = e.get("recorded_by")
        if (e.get("sha") == sha and not e.get("bypass")
                and (e.get("verdict") or "").upper() in APPROVING
                and (recorded_by is None or recorded_by == e.get("reviewer"))
                and e.get("reviewer") and e.get("reviewer") != exclude_actor):
            return True
    return False


def cmd_record(args):
    st = record(find_project_root(args.project), args.self_name, args.sha,
                args.verdict, args.target, args.note, args.bypass)
    if st == "actor_mismatch":
        print("ACTOR_MISMATCH: recorder environment does not match --self", file=sys.stderr)
        return 6
    if st != "ok":
        print("LOCKBUSY", file=sys.stderr)
        return 3
    return 0


def cmd_check(args):
    ok = has_approval(find_project_root(args.project), args.sha, args.exclude)
    if args.json:
        print('{"approved": %s}' % ("true" if ok else "false"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record")
    r.add_argument("--self", dest="self_name", required=True)
    r.add_argument("--project", default=None)
    r.add_argument("--sha", required=True)
    r.add_argument("--verdict", required=True)
    r.add_argument("--target", default=None)
    r.add_argument("--note", default=None)
    r.add_argument("--bypass", action="store_true")

    c = sub.add_parser("check")
    c.add_argument("--project", default=None)
    c.add_argument("--sha", required=True)
    c.add_argument("--exclude", required=True)
    c.add_argument("--json", action="store_true")

    args = ap.parse_args()
    return {"record": cmd_record, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
