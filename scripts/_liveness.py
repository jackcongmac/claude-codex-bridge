#!/usr/bin/env python3
"""_liveness.py — verdict on whether each agent can react to board changes.

WHY: a fresh presence heartbeat (last_seen) means the window/process is present,
but it does not mean the agent is listening for board changes. Reactivity requires
a fresh heartbeat AND an armed board-wait pidfile whose pid is alive.

Verdicts:
  REACTIVE — present (last_seen fresh) AND board-wait armed right now
  PRESENT  — present but not currently armed (re-arm or drive directly)
  STALE    — last_seen older than the present-window but younger than departure threshold
  DEAD     — last_seen older than the departure threshold
  DEPARTED — the participant carries the departed flag

This module is READ-ONLY. Notification and revive (side effects) live in
bridge-liveness.sh and are gated on the design review of DESIGN_liveness.md.

Subcommand:
  report --project DIR [--present-window SEC] [--stale-after SEC] [--json] [--self S]
"""
import argparse
import json
import os
import string
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root, read_json, _pid_alive  # noqa: E402


SAFE_AGENT_CHARS = set(string.ascii_letters + string.digits + "_.-")

LABELS = {
    "REACTIVE": "REACTIVE",
    "PRESENT": "PRESENT (not armed - re-arm or drive)",
    "STALE": "STALE",
    "DEAD": "DEAD",
    "DEPARTED": "DEPARTED",
}


def _age(last_seen, now):
    """Seconds since last_seen, or None if unparseable. Mirrors _presence.py."""
    try:
        t = time.mktime(datetime.strptime(
            last_seen.rsplit(" ", 1)[0], "%Y-%m-%d %H:%M:%S").timetuple())
        return now - t
    except Exception:
        return None


def _armed(collab_dir, name):
    """True iff this agent's board-wait pidfile exists and the pid is alive.
    Sanitize the name the same way board-wait.sh does (tr -c 'A-Za-z0-9_.-' '_')."""
    safe = "".join(
        chr(b) if chr(b) in SAFE_AGENT_CHARS else "_"
        for b in name.encode("utf-8")
    )
    pf = os.path.join(collab_dir, ".boardwait_%s.pid" % safe)
    try:
        with open(pf) as f:
            pid = f.read().strip()
        return bool(pid) and _pid_alive(int(pid))
    except (OSError, ValueError, TypeError):
        return False


def verdict(part, collab_dir, now, present_window, stale_after):
    name = part.get("name", "")
    armed = _armed(collab_dir, name)
    if part.get("departed"):
        v = "DEPARTED"
        return {"name": name, "verdict": v, "label": LABELS[v],
                "armed": armed, "reactive": False, "age": None}
    age = _age(part.get("last_seen", ""), now)
    if age is None:
        v = "STALE"                              # unparseable heartbeat: do not be optimistic
    elif age > stale_after:
        v = "DEAD"
    elif age > present_window:
        v = "STALE"
    elif armed:
        v = "REACTIVE"
    else:
        v = "PRESENT"                            # present but not currently armed
    reactive = (v == "REACTIVE")
    return {"name": name, "verdict": v, "label": LABELS[v],
            "armed": armed, "reactive": reactive,
            "age": (int(age) if age is not None else None)}


def report(args):
    project = find_project_root(args.project)
    p = collab_paths(project)
    reg = read_json(p["participants"], default={"participants": []}) or {"participants": []}
    now = time.time()
    # The short present-window is OPT-IN. By default present_window == stale_after,
    # so there is NO short STALE tier: last_seen only ticks while board-wait is armed,
    # so a busy/active (un-armed) agent's heartbeat ages — defaulting to a short window
    # would false-stale it (the bug shipped in e2c5f3d). A short window is only honest
    # once a presence-keepalive ticks last_seen independent of board-wait (next slice).
    present_window = (args.present_window if args.present_window is not None
                      else args.stale_after)
    rows = [verdict(a, p["dir"], now, present_window, args.stale_after)
            for a in reg.get("participants", []) if a.get("name")]
    if args.json:
        print(json.dumps(rows))
    else:
        for r in rows:
            age = ("%ds" % r["age"]) if r["age"] is not None else "?"
            mark = " (you)" if r["name"] == args.self_name else ""
            print("%-12s %-38s reactive=%-5s armed=%-5s age=%s%s"
                  % (r["name"], r["label"], str(r["reactive"]).lower(),
                     str(r["armed"]).lower(), age, mark))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--self", dest="self_name", default="")
    r.add_argument("--project", default=os.getcwd())
    r.add_argument("--present-window", type=float, default=None,
                   help="short-window seconds for the STALE tier; default = --stale-after "
                        "(no short STALE) until a presence-keepalive makes it honest")
    r.add_argument("--stale-after", type=float,
                   default=float(os.environ.get("BRIDGE_PRESENCE_STALE", 1800)))
    r.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return {"report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
