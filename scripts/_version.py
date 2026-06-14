#!/usr/bin/env python3
"""_version.py — is THIS clone behind the published bridge?

Powers `bridge-update.sh --check` and a join-time "newer version available" hint, so
a user on a stale clone is TOLD to update instead of silently running old code (the
bridge has no auto-update; distribution is a git clone). Best-effort and offline-safe:
any git/network failure -> unknown, never an exception that blocks join.

CLI:  _version.py check --repo DIR [--fetch] [--json]
"""
import argparse
import json
import os
import subprocess
import sys


def _git(repo, *args, timeout=8):
    try:
        return subprocess.run(["git", "-C", repo, *args],
                              capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def status(repo, fetch=False):
    """Return update status for `repo`. Keys: is_git, branch, local, remote,
    behind, ahead, dirty, error. Numeric fields are None when unknown."""
    out = {"is_git": False, "branch": None, "local": None, "remote": None,
           "behind": None, "ahead": None, "dirty": None, "error": None}
    r = _git(repo, "rev-parse", "--is-inside-work-tree")
    if r is None or r.returncode != 0 or r.stdout.strip() != "true":
        return out
    out["is_git"] = True
    br = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    out["branch"] = (br.stdout.strip() or None) if br else None
    hd = _git(repo, "rev-parse", "HEAD")
    out["local"] = (hd.stdout.strip() or None) if hd else None
    if fetch:
        fr = _git(repo, "fetch", "--quiet", timeout=20)
        if fr is None or fr.returncode != 0:
            # fetch failed (offline / broken remote): the tracking refs are STALE, so
            # behind/ahead would be misleading. Report unknown, never "up to date".
            out["error"] = "fetch-failed"
            return out
    up = _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    if up is None or up.returncode != 0:
        out["error"] = "no upstream"
        return out
    upstream = up.stdout.strip()
    ur = _git(repo, "rev-parse", upstream)
    out["remote"] = (ur.stdout.strip() or None) if ur else None
    counts = _git(repo, "rev-list", "--left-right", "--count", "HEAD...%s" % upstream)
    if counts and counts.returncode == 0 and counts.stdout.strip():
        try:
            ahead, behind = counts.stdout.split()
            out["ahead"], out["behind"] = int(ahead), int(behind)
        except ValueError:
            out["error"] = "count-parse"
    st = _git(repo, "status", "--porcelain")
    if st is not None:
        out["dirty"] = bool(st.stdout.strip())
    return out


def cmd_check(args):
    s = status(args.repo, fetch=args.fetch)
    if args.json:
        print(json.dumps(s))
        return 0
    if not s["is_git"]:
        print("not a git clone — update by re-installing from the repo")
        return 0
    if s["behind"] is None:
        print("update status unknown (%s)" % (s["error"] or "no upstream"))
        return 0
    if s["behind"] > 0:
        print("⚠ %d commit(s) behind %s — run bridge-update.sh to get the latest"
              % (s["behind"], s["branch"] or "upstream"))
    else:
        print("up to date with %s" % (s["branch"] or "upstream"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--repo",
                   default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    c.add_argument("--fetch", action="store_true")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return {"check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
