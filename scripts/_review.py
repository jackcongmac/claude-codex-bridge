#!/usr/bin/env python3
"""_review.py — the review ledger + approval check behind the gated push.

Mechanizes "review before merge" (non-negotiable #4): a reviewer records a verdict
bound to a specific commit SHA, and bridge-push refuses to push a SHA that no PEER
has approved. This converts a verbal "the peer reviewed it" claim into an auditable
artifact + a hard gate — exactly the gap that let an agent push unreviewed work and
attribute a review that never happened.

Honest boundary: until identity binding lands, `--self` is nominal, so a determined
author could still self-certify by recording an entry as the peer. The gate raises
the bar (auditable, requires a deliberate forged artifact) but is not anti-spoof on
its own — see DESIGN/roadmap.

CLI:
  _review.py record --self R --sha S --verdict V [--target T] [--note N] [--bypass]
  _review.py check  --sha S --exclude ACTOR [--json]   # exit 0 = approved, 1 = not
"""
import argparse
import os
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


def record(project, reviewer, sha, verdict, target=None, note=None, bypass=False,
           wait=10.0):
    """Append a review entry under the collaboration lock. Returns True/False."""
    p = collab_paths(project)
    entry = {"reviewer": reviewer, "sha": sha, "verdict": (verdict or "").upper(),
             "target": target, "note": note, "bypass": bool(bypass),
             "ts": now_str(), "nonce": _nonce()}
    if not acquire_lock(p["lock"], "review-%s" % reviewer, ttl=30, wait=wait):
        return False
    try:
        led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
        led.setdefault("reviews", []).append(entry)
        atomic_write_json(p["reviews"], led)
        return True
    finally:
        release_lock(p["lock"])


def has_approval(project, sha, exclude_actor):
    """True iff a NON-bypass approving (SHIP/GO) verdict exists for `sha` by a
    reviewer other than `exclude_actor` (you can't approve your own push)."""
    p = collab_paths(project)
    led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
    for e in led.get("reviews", []):
        if (e.get("sha") == sha and not e.get("bypass")
                and (e.get("verdict") or "").upper() in APPROVING
                and e.get("reviewer") and e.get("reviewer") != exclude_actor):
            return True
    return False


def cmd_record(args):
    ok = record(find_project_root(args.project), args.self_name, args.sha,
                args.verdict, args.target, args.note, args.bypass)
    if not ok:
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
