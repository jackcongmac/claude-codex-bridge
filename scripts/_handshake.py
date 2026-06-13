#!/usr/bin/env python3
"""_handshake.py — the pre-collaboration liveness handshake (ping/pong channel).

WHY: real collaboration between two running windows happens over the board, and
the peer only reacts because its board-wait.sh is ARMed in the background. If the
peer never ARMed (or its window is closed, or the transport is dead), a board post
gets no reaction and a blocking peer call hangs in silence. This channel turns that
silent hang into a fast, diagnosable handshake.

DESIGN (per Codex review of DESIGN_handshake.md):
  - A SEPARATE collaboration_handshake.json — NEVER bumps collaboration_signal.json.
    The signal's update_id keeps meaning "real collaboration content changed", so a
    handshake can never mask or steal a real board update.
  - The PONG is written by the PEER'S board-wait.sh (harness layer), not the peer
    agent. That is the whole point: it proves the ARM mechanism real collaboration
    depends on is actually alive, deterministically, with no reliance on the agent
    "remembering to reply".
  - The channel is NONCE-INDEXED: {"pings": {nonce: {...}}, "pongs": {nonce: {...}}}.
    Each ping is addressed to a SPECIFIC peer (`to`) and has an expiry. Inserting a
    ping never clears unrelated records, so two simultaneous handshakes cannot
    overwrite each other or produce a false NO-GO. Expired records are pruned
    opportunistically under the lock.
  - read-modify-write of the JSON is guarded by a small dedicated lock, never the
    main collaboration.lock.

Subcommands:
  ack  --self S --project DIR
       Used by board-wait each loop. If there is a live ping addressed to S with no
       matching pong, write the pong. Idempotent, silent, never raises out.
  ping --self S --peer P --project DIR [--ttl SEC]
       Initiator: insert a fresh nonce-keyed ping (pruning expired records, never
       clearing unrelated ones). Prints the nonce.
  poll --self S --peer P --nonce N --project DIR [--timeout SEC] [--interval SEC]
       Initiator: block until the peer's pong for N appears, or timeout.
       Prints "PONG rtt=<sec>" + exit 0, or "NOPONG" + exit 1.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import (  # noqa: E402
    collab_paths, read_json, atomic_write_json, acquire_lock, release_lock, now_str,
)


def _nonce():
    return "%x-%x" % (int(time.time() * 1000), os.getpid())


def _with_lock(paths, fn, wait=3.0, ttl=5):
    """Run fn() while holding the dedicated handshake lock. Returns True if the lock
    was acquired (and fn ran), False if it could not be taken in `wait` seconds.
    fn communicates results via closure, not its return value."""
    lock = paths["handshake_lock"]
    if not acquire_lock(lock, "handshake-%d" % os.getpid(), ttl=ttl, wait=wait):
        return False
    try:
        fn()
        return True
    finally:
        release_lock(lock)


def _read(paths):
    try:
        d = read_json(paths["handshake"], default={}) or {}
    except Exception:
        # corrupt handshake file is never fatal — treat as empty, it'll be rewritten
        d = {}
    # Normalize to the nonce-indexed shape; ignore any legacy single-slot keys.
    if not isinstance(d.get("pings"), dict):
        d["pings"] = {}
    if not isinstance(d.get("pongs"), dict):
        d["pongs"] = {}
    return d


def _prune(d, now):
    """Drop expired pings and any pong whose ping is gone — keeps the file small and
    stops a stale pong from satisfying a future same-nonce poll (nonces are unique)."""
    live = {n: p for n, p in d["pings"].items()
            if float(p.get("expires_at_epoch", 0)) >= now}
    d["pings"] = live
    d["pongs"] = {n: g for n, g in d["pongs"].items() if n in live}
    return d


def cmd_ack(args):
    """Called by board-wait. Pong every live ping addressed to me. Never raises out."""
    paths = collab_paths(args.project)
    try:
        now = time.time()
        data = _read(paths)
        todo = [n for n, p in data["pings"].items()
                if p.get("to") == args.self_name
                and float(p.get("expires_at_epoch", 0)) >= now
                and n not in data["pongs"]]
        if not todo:
            return 0

        def _write():
            d = _prune(_read(paths), time.time())
            for n, p in d["pings"].items():
                if (p.get("to") == args.self_name and n not in d["pongs"]
                        and float(p.get("expires_at_epoch", 0)) >= time.time()):
                    d["pongs"][n] = {"from": args.self_name, "nonce": n,
                                     "pong_at": now_str()}
            atomic_write_json(paths["handshake"], d)
        _with_lock(paths, _write)
    except Exception:
        pass  # a handshake hiccup must never break the board-wait loop
    return 0


def cmd_ping(args):
    paths = collab_paths(args.project)
    nonce = _nonce()
    now = time.time()

    def _write():
        d = _prune(_read(paths), now)
        d["pings"][nonce] = {
            "from": args.self_name, "to": args.peer, "nonce": nonce,
            "created_at": now_str(), "expires_at_epoch": now + args.ttl,
        }
        atomic_write_json(paths["handshake"], d)
    if not _with_lock(paths, _write):
        print("LOCKFAIL", file=sys.stderr)
        return 3
    print(nonce)
    return 0


def cmd_poll(args):
    paths = collab_paths(args.project)
    start = time.time()
    deadline = start + args.timeout
    while True:
        pong = _read(paths)["pongs"].get(args.nonce) or {}
        if pong.get("from") == args.peer:
            print("PONG %.1f" % (time.time() - start))
            return 0
        if time.time() >= deadline:
            print("NOPONG")
            return 1
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--self", dest="self_name", required=True)
        p.add_argument("--project", default=os.getcwd())

    a = sub.add_parser("ack"); common(a)
    pi = sub.add_parser("ping"); common(pi)
    pi.add_argument("--peer", required=True)
    pi.add_argument("--ttl", type=float, default=30.0)
    po = sub.add_parser("poll"); common(po)
    po.add_argument("--peer", required=True)
    po.add_argument("--nonce", required=True)
    po.add_argument("--timeout", type=float, default=15.0)
    po.add_argument("--interval", type=float, default=1.0)

    args = ap.parse_args()
    return {"ack": cmd_ack, "ping": cmd_ping, "poll": cmd_poll}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
