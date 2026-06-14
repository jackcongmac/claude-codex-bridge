#!/usr/bin/env bash
#
# bridge-liveness.sh — at-a-glance, continuously, whether each agent is alive.
#
# THE fix for "我还是不确定你俩联通了没有": board-wait is one-shot, so judging
# liveness by its pidfile false-flags a present, reactive agent as DEAD during the
# brief re-arm gap. This reports a PRESENCE-based verdict (last_seen heartbeat) so a
# present agent reads PRESENT/LIVE — never a misleading DEAD — with ARMED shown as a
# secondary detail.
#
# Verdicts: LIVE (present + armed) · PRESENT (present, in the re-arm gap) ·
#           STALE (heartbeat aging) · DEAD (past departure threshold) · DEPARTED.
#
# Usage:
#   bridge-liveness.sh [report] [--self S] [--project DIR] [--watch] [--interval N]
#                      [--present-window SEC] [--stale-after SEC] [--json]
#
# READ-ONLY. Notify-on-death and revive (side effects) are deferred to a separate
# change pending the DESIGN_liveness.md review — this command never writes.
set -euo pipefail

SELF=""; PROJECT="$PWD"; WATCH=0; INTERVAL="${BRIDGE_LIVENESS_INTERVAL:-5}"; JSON=0
PRESENT_WINDOW="${BRIDGE_PRESENT_WINDOW:-60}"
STALE_AFTER="${BRIDGE_PRESENCE_STALE:-1800}"
while [ $# -gt 0 ]; do
  case "$1" in
    report) shift;;
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --watch) WATCH=1; shift;;
    --interval) INTERVAL="$2"; shift 2;;
    --present-window) PRESENT_WINDOW="$2"; shift 2;;
    --stale-after) STALE_AFTER="$2"; shift 2;;
    --json) JSON=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

run_once() {
  if [ "$JSON" = "1" ]; then
    "$PY3" "$HERE/_liveness.py" report --self "$SELF" --project "$PROJECT" \
      --present-window "$PRESENT_WINDOW" --stale-after "$STALE_AFTER" --json
  else
    echo "Liveness — $BRIDGE_COLLAB  (present-window=${PRESENT_WINDOW}s)"
    "$PY3" "$HERE/_liveness.py" report --self "$SELF" --project "$PROJECT" \
      --present-window "$PRESENT_WINDOW" --stale-after "$STALE_AFTER"
  fi
}

if [ "$WATCH" = "1" ]; then
  while :; do
    clear 2>/dev/null || true
    date '+%Y-%m-%d %H:%M:%S %Z'
    run_once
    sleep "$INTERVAL"
  done
else
  run_once
fi
