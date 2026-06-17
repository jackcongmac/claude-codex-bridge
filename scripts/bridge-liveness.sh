#!/usr/bin/env bash
#
# bridge-liveness.sh — at-a-glance, continuously, whether each agent is alive.
#
# THE fix for "我还是不确定你俩联通了没有": judging liveness by the board-wait
# pidfile alone false-flags a present agent as DEAD whenever the listener is
# temporarily unarmed (legacy wake-on-exit, active work, or a missed re-arm). This
# reports a PRESENCE-based verdict (last_seen heartbeat) so a present agent reads
# PRESENT/LIVE — never a misleading DEAD — with ARMED shown as a secondary detail.
#
# Verdicts: LIVE (present + armed) · PRESENT (present but not currently armed) ·
#           STALE (heartbeat aging — only with an explicit short --present-window) ·
#           DEAD (past departure threshold) · DEPARTED.
#
# Usage:
#   bridge-liveness.sh [report] [--self S] [--project DIR] [--watch] [--interval N]
#                      [--present-window SEC] [--stale-after SEC] [--json] [--notify]
#
# READ-ONLY by default. `--notify` (requires --self) OPTS IN to side effects: on each
# pass it pages once when a peer TRANSITIONS into DEAD/DEPARTED (OS notify + a board
# "## Liveness" note), debounced via _notify.py. Revive is a separate command
# (bridge-revive.sh).
set -euo pipefail

SELF=""; PROJECT="$PWD"; WATCH=0; INTERVAL="${BRIDGE_LIVENESS_INTERVAL:-5}"; JSON=0; NOTIFY=0
# Empty present-window = let _liveness default it to --stale-after (NO short STALE
# tier): last_seen only ticks while board-wait is armed, so defaulting to a short
# window would false-stale a busy/active agent (the bug shipped in e2c5f3d). A short
# window is opt-in and only honest once a presence-keepalive lands.
PRESENT_WINDOW="${BRIDGE_PRESENT_WINDOW:-}"
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
    --notify) NOTIFY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
if [ "$NOTIFY" = "1" ] && [ -z "$SELF" ]; then
  echo "[x] --notify requires --self <Me> (who is doing the detecting)" >&2; exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

# Only pass --present-window when explicitly set; otherwise _liveness defaults it to
# --stale-after. Unquoted expansion keeps the empty case a no-op under `set -u`.
PW_OPT=""
[ -n "$PRESENT_WINDOW" ] && PW_OPT="--present-window $PRESENT_WINDOW"
PW_LABEL="${PRESENT_WINDOW:+${PRESENT_WINDOW}s}"; PW_LABEL="${PW_LABEL:-default(=${STALE_AFTER}s, no short STALE)}"

run_once() {
  if [ "$JSON" = "1" ]; then
    "$PY3" "$HERE/_liveness.py" report --self "$SELF" --project "$PROJECT" \
      $PW_OPT --stale-after "$STALE_AFTER" --json
  else
    echo "Liveness — $BRIDGE_COLLAB  (present-window=${PW_LABEL})"
    "$PY3" "$HERE/_liveness.py" report --self "$SELF" --project "$PROJECT" \
      $PW_OPT --stale-after "$STALE_AFTER"
  fi
}

# Opt-in side effect: page once on a peer's transition into DEAD/DEPARTED (debounced).
maybe_notify() {
  [ "$NOTIFY" = "1" ] || return 0
  # Send _notify's "NOTIFY ..." line to stderr so it never corrupts --json stdout.
  "$PY3" "$HERE/_notify.py" tick --self "$SELF" --project "$PROJECT" \
    $PW_OPT --stale-after "$STALE_AFTER" 1>&2 || true
}

if [ "$WATCH" = "1" ]; then
  while :; do
    clear 2>/dev/null || true
    date '+%Y-%m-%d %H:%M:%S %Z'
    run_once
    maybe_notify
    sleep "$INTERVAL"
  done
else
  run_once
  maybe_notify
fi
