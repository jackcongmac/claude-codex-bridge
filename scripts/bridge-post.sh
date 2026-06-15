#!/usr/bin/env bash
#
# bridge-post.sh — transactional board post: locked append-to-your-outbox + signal
# bump in one atomic step. Use this instead of hand-editing collaboration.md and
# bumping the signal separately (that lock-free pattern loses updates / leaves a
# stale signal). Exit 3 = lock busy (nothing written) — retry.
#
# Usage:
#   bridge-post.sh --self <Me> --message "..." [--project DIR]
#                  [--section "<board section>"] [--summary "..."] [--wait SEC]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"

SELF=""; PROJECT="$PWD"; MESSAGE=""; SECTION=""; SUMMARY=""; WAIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --message) MESSAGE="$2"; shift 2;;
    --section) SECTION="$2"; shift 2;;
    --summary) SUMMARY="$2"; shift 2;;
    --wait) WAIT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <Me> required" >&2; exit 2; }
[ -n "$MESSAGE" ] || { echo "[x] --message required" >&2; exit 2; }

bridge_resolve "$PROJECT"; PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

ARGS=(post --self "$SELF" --project "$PROJECT" --message "$MESSAGE")
[ -n "$SECTION" ] && ARGS+=(--section "$SECTION")
[ -n "$SUMMARY" ] && ARGS+=(--summary "$SUMMARY")
[ -n "$WAIT" ] && ARGS+=(--wait "$WAIT")
exec "$PY3" "$HERE/_post.py" "${ARGS[@]}"
