#!/usr/bin/env python3
"""_presence.py — presence heartbeat + departure broadcast for the collaboration.

One "tick" of the presence layer, called each board-wait cycle:
1. Refresh MY last_seen in collaboration_participants.json (heartbeat = "I'm here").
2. Scan peers: any participant whose last_seen is older than --stale-after and not
   already marked departed is considered GONE (its window closed). The FIRST agent
   to detect it (under the global lock) marks it departed=true AND BROADCASTS a
   departure note to the board's "## Participants" section + bumps the signal, so
   every other armed agent wakes and learns that peer left. Dedup: marking
   departed under the lock means only one broadcast per departure.

NOTE on the threshold: heartbeat (last_seen refresh) only happens while an agent
is CONTINUOUSLY running board-wait. Interactive chat agents act in BURSTS, so the
default --stale-after is generous (1800s / 30 min) — a shorter window
false-flags a present-but-bursty agent as departed. A departed flag self-heals:
any join/board write by that agent sets departed=false again.

Usage: _presence.py tick --self NAME --project DIR [--stale-after SEC]
Prints "DEPARTED <name>" for each peer it broadcasts as gone (else nothing).
"""
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_common as bc


def _age(last_seen, now):
    # last_seen is a "%Y-%m-%d %H:%M:%S %Z" string; compare via mtime fallback isn't
    # available, so parse the leading 19 chars as local time.
    try:
        t = time.mktime(time.strptime(last_seen[:19], "%Y-%m-%d %H:%M:%S"))
        return now - t
    except Exception:
        return 0.0   # unparseable -> treat as fresh (don't false-positive a departure)


def tick(project, self_name, stale_after):
    P = bc.collab_paths(project)
    parts_p = P["participants"]
    board_p = P["board"]
    signal_p = P["signal"]
    lock_p = P["lock"]
    now = time.time()
    now_s = bc.now_str()

    # 1) heartbeat: refresh my last_seen (best-effort, no lock needed for my own row;
    #    a torn read just costs one cycle).
    reg = bc.read_json(parts_p, {"participants": []}) or {"participants": []}
    mine = next((a for a in reg["participants"] if a.get("name") == self_name), None)
    if mine is None:
        reg["participants"].append({"name": self_name, "role": "peer",
                                    "joined_at": now_s, "last_seen": now_s, "departed": False})
    else:
        mine["last_seen"] = now_s
        mine["departed"] = False
    try:
        bc.atomic_write_json(parts_p, reg)
    except Exception:
        pass

    # 2) departure scan
    gone = [a for a in reg["participants"]
            if a.get("name") != self_name and not a.get("departed")
            and _age(a.get("last_seen", ""), now) > stale_after]
    if not gone:
        return []

    broadcast = []
    if not bc.acquire_lock(lock_p, "presence-%s" % self_name, ttl=120, wait=10):
        return []   # someone else holds it; they'll handle the broadcast
    try:
        reg = bc.read_json(parts_p, {"participants": []}) or {"participants": []}
        changed = False
        for a in reg["participants"]:
            if (a.get("name") != self_name and not a.get("departed")
                    and _age(a.get("last_seen", ""), now) > stale_after):
                a["departed"] = True
                broadcast.append(a["name"])
                changed = True
        if changed:
            bc.atomic_write_json(parts_p, reg)
            note = "**Departure detected** by %s: %s went silent (>%ds since last_seen) — window likely closed. Re-route their open work; they must re-run join-collaboration.sh to return." % (
                self_name, ", ".join(broadcast), int(stale_after))
            bc._append_under_header(board_p, "## Participants", note)
            prev = int((bc.read_json(signal_p, {}) or {}).get("update_id", 0))
            bc.atomic_write_json(signal_p, {
                "update_id": prev + 1, "updated_at": now_s, "updated_by": self_name,
                "changed_section": "Participants",
                "summary": "Departure broadcast: %s left" % ", ".join(broadcast)})
            bc.log_event(project, "participant_departed", run_id="presence",
                         detector=self_name, departed=broadcast)
    finally:
        bc.release_lock(lock_p)
    return broadcast


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--self", dest="self_name", required=True)
    t.add_argument("--project", default=None)
    t.add_argument("--stale-after", type=int, default=int(os.environ.get("BRIDGE_PRESENCE_STALE", "1800")))
    a = ap.parse_args()
    if a.cmd == "tick":
        proj = os.path.abspath(a.project) if a.project else bc.find_project_root()
        for name in tick(proj, a.self_name, a.stale_after):
            print("DEPARTED %s" % name)


if __name__ == "__main__":
    main()
