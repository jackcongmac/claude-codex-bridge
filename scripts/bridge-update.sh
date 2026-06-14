#!/usr/bin/env bash
#
# bridge-update.sh — pull the latest bridge into THIS clone (the bridge has no
# auto-update; distribution is a git clone, so this is the "get the newest version"
# command). Scripts + the skill are referenced live from the clone, so a pull makes
# them current immediately; the MCP wrapper needs a Codex restart (and, until it's a
# symlink, a re-run of install.sh) to reload.
#
# Usage:
#   bridge-update.sh            # git pull --ff-only into this clone
#   bridge-update.sh --check    # just report whether a newer version is available
#   bridge-update.sh --repo DIR # operate on another clone (default: this repo)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PY3="$(command -v python3)"
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift;;
    --repo) REPO="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

if [ "$CHECK" = "1" ]; then
  "$PY3" "$HERE/_version.py" check --repo "$REPO" --fetch
  exit 0
fi

if ! git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[x] $REPO is not a git clone — can't auto-update. Re-install from the repo." >&2
  exit 1
fi

if [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]; then
  echo "[!] working tree has uncommitted changes — git will refuse to fast-forward if"
  echo "    they conflict. Commit/stash first if the pull is blocked."
fi
echo "[==>] updating $REPO"
git -C "$REPO" pull --ff-only
echo "[ok] updated — scripts and the skill are now current."
echo "[!] If the MCP wrapper changed: restart Codex to reload it (and re-run ./install.sh"
echo "    if your installed wrapper is a copy rather than a symlink)."
