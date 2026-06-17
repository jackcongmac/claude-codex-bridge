#!/usr/bin/env python3
"""_post.py — the transactional manual board write.

Manual collaboration mode otherwise tells agents to hand-edit collaboration.md and
then bump collaboration_signal.json separately — a lock-free read-modify-write that
loses updates and can leave a stale/empty signal. This does BOTH under the
collaboration lock with the full signal schema and correct stamping (the same
discipline the autonomous path already has). Two files can't be truly atomic, so the
writes are ordered content-first — the only failure leaves the message on the board
(recoverable), never a lost message or a phantom wake (see post()). It is the
primitive the trust-mechanization layer (review ledger, gated push) builds on.

CLI: _post.py post --self S --message M [--project DIR] [--section SEC]
                   [--summary T] [--wait SEC]
Exit 0 = posted. 2 = bad args. 3 = lock busy (nothing written). 4 = posted to the
board but the signal bump failed (re-run to bump; content is preserved).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, _append_under_header, read_json,
    atomic_write_json, now_str, acquire_lock, release_lock,
)


def post(project, self_name, message, section=None, summary=None, wait=10.0, guard=None):
    """Locked append-to-section + signal bump. Returns a status string:
      "ok"            — board appended AND signal bumped
      "lockbusy"      — lock not taken within `wait`; NOTHING written
      "superseded"    — `guard` rejected the current board UNDER the lock; NOTHING
                        written (compare-and-append: e.g. the chat responder dropping
                        a reply that a newer message raced ahead of)
      "signal_failed" — board appended but the signal bump failed (rare: corrupt/
                        unwritable signal). Content-first ordering means the message
                        is preserved (never a phantom wake or a lost message); the
                        caller must re-bump so the peer wakes.
    `guard`, if given, is called with the current board text WHILE THE LOCK IS HELD;
    returning False aborts the write atomically — no other writer can interleave
    between the check and the append, since every writer takes this same lock.
    True two-file atomicity isn't possible across two files; this orders the writes so
    the only failure leaves content on the board (recoverable), not the reverse."""
    p = collab_paths(project)
    sec = section or ("%s Outbox" % self_name)
    if not acquire_lock(p["lock"], "post-%s" % self_name, ttl=30, wait=wait):
        return "lockbusy"
    try:
        if guard is not None:
            try:
                with open(p["board"]) as f:
                    current = f.read()
            except OSError:
                current = ""
            if not guard(current):
                return "superseded"
        _append_under_header(p["board"], "## %s" % sec, message)
        try:
            prev = int((read_json(p["signal"], default={}) or {}).get("update_id", 0))
            atomic_write_json(p["signal"], {
                "update_id": prev + 1, "updated_at": now_str(),
                "updated_by": self_name, "changed_section": sec,
                "summary": (summary or message)[:200],
            })
        except Exception:
            return "signal_failed"
        return "ok"
    finally:
        release_lock(p["lock"], "post-%s" % self_name)


def cmd_post(args):
    project = find_project_root(args.project)
    st = post(project, args.self_name, args.message, args.section,
              args.summary, args.wait)
    if st == "lockbusy":
        print("LOCKBUSY", file=sys.stderr)
        return 3
    if st == "signal_failed":
        print("POSTED_NO_SIGNAL: your message is on the board but the signal was NOT "
              "bumped — re-run bridge-post so the peer wakes.", file=sys.stderr)
        return 4
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post")
    p.add_argument("--self", dest="self_name", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--message", required=True)
    p.add_argument("--section", default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--wait", type=float, default=10.0)
    args = ap.parse_args()
    return {"post": cmd_post}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
