#!/usr/bin/env python3
"""_notify.py — page when an agent TRANSITIONS into DEAD/DEPARTED.

Debounced via a LOCKED .collab/.liveness_seen.json (per-agent last verdict). Fires
ONLY on a transition INTO {DEAD, DEPARTED} — never STALE, never recovery, and never
on first sight (bootstrap records the current verdicts quietly so starting a watcher
next to an already-dead peer doesn't page).

Detect + the board note + the seen-commit all happen under ONE hold of the
collaboration lock — deliberately NOT by shelling out to bridge-post (which would
re-acquire the same lock and deadlock). The OS notify() fires AFTER the lock is
released. Bumping the signal on a liveness note is intentional: other reactive agents'
board-wait wakes and learns of the departure (same as the presence departure
broadcast).

CLI: _notify.py tick --self S --project DIR [--present-window N] [--stale-after N]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_common as bc  # noqa: E402
from _liveness import verdict as _verdict  # noqa: E402

DEAD = {"DEAD", "DEPARTED"}


def tick(project, self_name, present_window, stale_after):
    P = bc.collab_paths(project)
    seen_path = os.path.join(P["dir"], ".liveness_seen.json")
    now = time.time()
    fired = []
    if not bc.acquire_lock(P["lock"], "notify-%s" % self_name, ttl=30, wait=10):
        return fired
    try:
        reg = bc.read_json(P["participants"], {"participants": []}) or {"participants": []}
        cur = {}
        for a in reg.get("participants", []):
            if a.get("name"):
                cur[a["name"]] = _verdict(a, P["dir"], now, present_window,
                                          stale_after)["verdict"]
        seen = bc.read_json(seen_path, {}) or {}
        for name, v in cur.items():
            if name == self_name:
                continue                          # never page about myself
            prev = seen.get(name)
            if prev is not None and prev not in DEAD and v in DEAD:
                note = "**%s went %s** — detected by %s." % (name, v, self_name)
                bc._append_under_header(P["board"], "## Liveness", note)
                s = bc.read_json(P["signal"], {}) or {}
                bc.atomic_write_json(P["signal"], {
                    "update_id": int(s.get("update_id", 0)) + 1,
                    "updated_at": bc.now_str(), "updated_by": self_name,
                    "changed_section": "Liveness", "summary": "%s went %s" % (name, v)})
                fired.append((name, v))
        bc.atomic_write_json(seen_path, cur)      # commit (records bootstrap + recovery quietly)
    finally:
        bc.release_lock(P["lock"], "notify-%s" % self_name)
    for name, v in fired:                          # OS notify outside the lock
        try:
            bc.notify("liveness: %s went %s" % (name, v))
        except Exception:
            pass
    return fired


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--self", dest="self_name", required=True)
    t.add_argument("--project", default=None)
    t.add_argument("--present-window", type=float, default=None)
    t.add_argument("--stale-after", type=float,
                   default=float(os.environ.get("BRIDGE_PRESENCE_STALE", 1800)))
    a = ap.parse_args()
    proj = bc.find_project_root(a.project)
    pw = a.present_window if a.present_window is not None else a.stale_after
    for name, v in tick(proj, a.self_name, pw, a.stale_after):
        print("NOTIFY %s %s" % (name, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
