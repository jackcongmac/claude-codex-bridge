#!/usr/bin/env python3
"""bridge-doctor.py — diagnose (and optionally repair) a half-broken .collab.

Checks for the states that strand a collaboration:
  - stale LOCKS (collaboration.lock / handshake / .bridge_push.lock) whose holder PID
    is dead — but ONLY on THIS host (a live lock on another host must never be broken).
  - stale PIDFILES (.boardwait_* / .keepalive_*) whose PID is dead.
  - participants marked DEPARTED while their last_seen is still fresh.

Default is READ-ONLY (diagnose + report). `--fix` applies the safe repairs. Exit 0 =
nothing wrong / everything fixed; non-zero = issues remain (found, or unfixable like a
foreign-host lock).

Usage: bridge-doctor.py [--project DIR] [--fix] [--json]
"""
import argparse
import glob
import json
import os
import socket
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, find_project_root, read_json, atomic_write_json,
    release_lock, _pid_alive,
)

THIS_HOST = socket.gethostname()


def _age(last_seen, now):
    try:
        return now - time.mktime(time.strptime(last_seen[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def diagnose(project, stale_after=1800):
    """Return (issues, paths). Each issue: {kind, path?, name?, detail, fixable}."""
    p = collab_paths(project)
    issues = []

    lock_files = [p["lock"], p["handshake_lock"],
                  os.path.join(p["root"], ".bridge_push.lock")]
    for lf in lock_files:
        if not os.path.exists(lf):
            continue
        info = read_json(lf, {}) or {}
        pid = info.get("pid")
        host = info.get("host")
        if pid is not None and _pid_alive(pid):
            continue                                   # genuinely held — fine
        if host == THIS_HOST:                          # only break a THIS-host dead lock
            issues.append({"kind": "stale-lock", "path": lf, "fixable": True,
                           "detail": "lock holder pid %s is dead on this host" % pid})
        else:                                          # foreign or no host → cannot verify
            where = ("held on host '%s'" % host) if host else "no host recorded"
            issues.append({"kind": "lock-unverifiable", "path": lf, "fixable": False,
                           "detail": "%s — dead here but NOT broken (can't verify)" % where})

    for pf in sorted(glob.glob(os.path.join(p["dir"], ".boardwait_*.pid"))
                     + glob.glob(os.path.join(p["dir"], ".keepalive_*.pid"))):
        try:
            pid = int(open(pf).read().strip())
        except Exception:
            pid = None
        if pid is None or not _pid_alive(pid):
            issues.append({"kind": "stale-pidfile", "path": pf, "fixable": True,
                           "detail": "pidfile points at dead pid %s" % pid})

    reg = read_json(p["participants"], {"participants": []}) or {"participants": []}
    now = time.time()
    for a in reg.get("participants", []):
        if a.get("departed"):
            age = _age(a.get("last_seen", ""), now)
            if age is not None and age <= stale_after:
                issues.append({"kind": "departed-but-fresh", "name": a.get("name"),
                               "fixable": True,
                               "detail": "marked departed but last_seen is %ds old (present)" % int(age)})
    return issues, p


def _lock_still_stale(lf):
    """Re-validate at fix time: True only if the lock STILL exists, is on this host,
    and its pid is dead — so a lock a live owner re-acquired since diagnosis isn't nuked."""
    info = read_json(lf, None)
    if info is None:
        return False
    pid = info.get("pid")
    return info.get("host") == THIS_HOST and not (pid is not None and _pid_alive(pid))


def _pidfile_still_dead(pf):
    try:
        return not _pid_alive(int(open(pf).read().strip()))
    except Exception:
        return True   # unreadable/empty stale file — safe to remove


def _doctor_acquire(lock_path, wait=10.0):
    """Like bridge_common.acquire_lock but HOST-AWARE and atomic: on contention it
    breaks the lock ONLY if it is owned by THIS host with a dead pid — never a foreign
    or hostless lock (the break decision happens at acquire time, so there's no
    pre-check/acquire race that a host-blind acquire would have)."""
    payload = json.dumps({"pid": os.getpid(), "host": THIS_HOST,
                          "run_id": "doctor", "acquired_at": time.time()})
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, payload.encode())
            os.close(fd)
            return True
        except FileExistsError:
            info = read_json(lock_path, None)
            if info is not None:
                pid = info.get("pid")
                if info.get("host") == THIS_HOST and not (pid is not None and _pid_alive(pid)):
                    try:
                        os.unlink(lock_path)   # this-host dead → safe to break
                        continue
                    except OSError:
                        pass
                # foreign / hostless / alive → NEVER break
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def repair(project, issues, p, stale_after):
    """Apply the fixable repairs; return the issues that remain unfixed."""
    remaining = []
    departed = set()
    for i in issues:
        if not i.get("fixable"):
            remaining.append(i)
            continue
        if i["kind"] == "stale-lock":
            if _lock_still_stale(i["path"]):
                try:
                    os.unlink(i["path"])
                except OSError:
                    remaining.append(i)
            else:
                remaining.append(i)            # changed under us — leave it
        elif i["kind"] == "stale-pidfile":
            if _pidfile_still_dead(i["path"]):
                try:
                    os.unlink(i["path"])
                except OSError:
                    remaining.append(i)
            else:
                remaining.append(i)
        elif i["kind"] == "departed-but-fresh":
            departed.add(i["name"])

    if departed:
        # clear departed flags under the lock — but only if the lock isn't a foreign
        # one sitting there (acquire_lock breaks dead-LOCAL-pid locks, which would also
        # break a foreign lock whose pid looks dead here).
        if not _doctor_acquire(p["lock"], wait=10):
            remaining.extend(i for i in issues if i["kind"] == "departed-but-fresh")
        else:
            try:
                reg = read_json(p["participants"], {"participants": []}) or {"participants": []}
                now = time.time()
                for a in reg.get("participants", []):
                    if a.get("name") in departed and a.get("departed"):
                        age = _age(a.get("last_seen", ""), now)
                        if age is not None and age <= stale_after:
                            a["departed"] = False
                atomic_write_json(p["participants"], reg)
            finally:
                release_lock(p["lock"], "doctor")
    return remaining


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stale-after", type=int,
                    default=int(os.environ.get("BRIDGE_PRESENCE_STALE", "1800")))
    a = ap.parse_args()
    project = find_project_root(a.project)
    issues, p = diagnose(project, a.stale_after)
    remaining = repair(project, issues, p, a.stale_after) if a.fix else issues

    if a.json:
        print(json.dumps({"fixed": a.fix, "issues": issues, "remaining": remaining}))
    else:
        if not issues:
            print("bridge-doctor: no issues found in %s" % p["dir"])
        else:
            for i in issues:
                where = i.get("path") or i.get("name") or ""
                tag = "FIXED" if (a.fix and i not in remaining) else (
                    "FIX-ME" if i.get("fixable") else "MANUAL")
                print("  [%s] %s: %s  %s" % (tag, i["kind"], where, i["detail"]))
            if a.fix:
                print("bridge-doctor: %d issue(s), %d remaining."
                      % (len(issues), len(remaining)))
            else:
                print("bridge-doctor: %d issue(s) — re-run with --fix to repair." % len(issues))
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
