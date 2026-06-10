#!/usr/bin/env python3
"""_compact.py — bounded-board archive rotation (large-project token control).

When collaboration.md grows past a threshold, move OLD entries out of the
log-like sections (Outboxes, Coverage Log, Improvement Proposals, Decision Log)
into collaboration_archive/, keeping only the most recent K entries per section.
Lossless: archived content is appended to a month-bucketed archive file, never
deleted. Non-log sections (Digest, roles, Current Task, Open Questions, File
Locks) are left intact, and the section named by the CURRENT pending signal
(changed_section) is NOT touched, so an unconsumed turn still finds its content.

Runs under the global lock and does NOT bump collaboration_signal.json (so it is
maintenance, not a turn — watchers are unaffected and no pending turn is lost).

Usage: scripts/compact-collaboration.sh [project]  (preferred)
       _compact.py --project DIR [--keep K] [--force]
"""
import os
import sys
import time
import argparse
import importlib.util

# Reuse the harness's lock / atomic-write / json / log helpers.
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_auto_turn", os.path.join(_HERE, "_auto_turn.py"))
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)

BOARD_MAX = int(os.environ.get("BRIDGE_BOARD_MAX_BYTES", str(64 * 1024)))
DEFAULT_KEEP = int(os.environ.get("BRIDGE_KEEP_ENTRIES", "5"))


def is_log_header(header):
    h = header.strip()
    return h.endswith(" Outbox") or h in ("## Coverage Log", "## Improvement Proposals", "## Decision Log")


def rotate(text, keep, protected):
    """Return (new_board_text, archived_text, archived_count)."""
    lines = text.split("\n")
    result, archived, count = [], [], 0
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.startswith("## "):
            result.append(line); i += 1
            continue
        header = line
        j = i + 1
        sec = []
        while j < n and not lines[j].startswith("## "):
            sec.append(lines[j]); j += 1
        i = j
        # Only rotate log-like sections that are NOT the pending (protected) one.
        sect_name = header[3:].strip()
        if not (is_log_header(header) and sect_name != (protected or "").strip()):
            result.append(header); result.extend(sec); continue
        # Split the section body into a preamble + '### ' entries.
        pre, entries, cur = [], [], None
        for s in sec:
            if s.startswith("### "):
                if cur is not None:
                    entries.append(cur)
                cur = [s]
            elif cur is not None:
                cur.append(s)
            else:
                pre.append(s)
        if cur is not None:
            entries.append(cur)
        if len(entries) <= keep:
            result.append(header); result.extend(sec); continue
        old, recent = entries[:-keep], entries[-keep:]
        count += len(old)
        archived.append(header + "\n" + "\n".join("\n".join(e) for e in old).rstrip() + "\n")
        result.append(header)
        result.extend(pre)
        for e in recent:
            result.extend(e)
    return "\n".join(result), "\n".join(archived), count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--force", action="store_true", help="rotate even if under the size threshold")
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    board_p = os.path.join(project, "collaboration.md")
    signal_p = os.path.join(project, "collaboration_signal.json")
    lock_p = os.path.join(project, "collaboration.lock")
    archive_dir = os.path.join(project, "collaboration_archive")

    if not os.path.exists(board_p):
        print("no collaboration.md"); sys.exit(0)
    size = os.path.getsize(board_p)
    if size < BOARD_MAX and not a.force:
        print("board %d bytes < %d threshold; nothing to do" % (size, BOARD_MAX)); sys.exit(0)

    if not at.acquire_lock(lock_p, "compact", ttl=600, wait=30):
        print("lock busy; skipping compaction"); sys.exit(10)
    try:
        sig = at.read_json(signal_p, {}) or {}
        protected = sig.get("changed_section", "")   # don't archive the pending section
        keep = max(1, a.keep)                          # keep >= 1 (avoid the -0 slice footgun)
        text = open(board_p).read()
        new_board, archived_text, count = rotate(text, keep, protected)
        if count == 0:
            print("nothing old enough to archive"); sys.exit(0)
        os.makedirs(archive_dir, exist_ok=True)
        ar_p = os.path.join(archive_dir, "collaboration_%s.md" % time.strftime("%Y-%m"))
        # Write the archive ATOMICALLY and BEFORE shrinking the board: a crash in
        # between can only DUPLICATE an archive (the board is still intact), never
        # lose content. This preserves the lossless invariant.
        existing = open(ar_p).read() if os.path.exists(ar_p) else "# Collaboration archive\n"
        block = "\n<!-- archived %s (kept last %d per section) -->\n%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S %Z"), keep, archived_text)
        at.atomic_write(ar_p, existing + block)
        at.atomic_write(board_p, new_board)
        # IMPORTANT: do NOT bump collaboration_signal.json — this is maintenance.
        at.log_event(project, "board_compacted", run_id="compact",
                     archived_entries=count, kept=keep,
                     before_bytes=size, after_bytes=len(new_board))
        print("archived %d entries -> %s (board %d -> %d bytes)" % (count, ar_p, size, len(new_board)))
    finally:
        at.release_lock(lock_p)


if __name__ == "__main__":
    main()
