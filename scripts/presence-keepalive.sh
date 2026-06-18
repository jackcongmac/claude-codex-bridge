#!/usr/bin/env bash
#
# presence-keepalive.sh — keep MY last_seen fresh while my window is open, so the
# collaboration can tell "alive" from "gone" at any instant.
#
# WHY: last_seen is otherwise only refreshed while board-wait.sh is armed. During an
# active turn (board-wait not armed) last_seen ages, so a busy-but-alive agent looks
# like it's going away. This tiny always-on loop refreshes last_seen on a short
# interval INDEPENDENT of board-wait — which is what lets bridge-liveness.sh use a
# short, trustworthy present-window (distinguishing a busy window from a closed one).
#
# Each cycle refreshes the heartbeat, then runs small idempotent safety drivers:
# inbox SLA escalation and peer departure detection. Single-instance per
# (project, self), same atomic-pidfile mutex as board-wait.
#
# Usage:
#   presence-keepalive.sh --self <Me> [--project DIR] [--interval SEC]
#   (run it in the BACKGROUND, alongside board-wait, for the whole session)
set -euo pipefail

SELF=""; PROJECT="$PWD"; INTERVAL="${BRIDGE_KEEPALIVE_INTERVAL:-10}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <Me> required" >&2; exit 2; }
# Reject a non-positive/non-numeric interval — otherwise `sleep 0` would busy-spin.
if ! awk -v x="$INTERVAL" 'BEGIN{exit !(x>0)}' 2>/dev/null; then
  echo "[x] --interval must be a positive number (got '$INTERVAL')" >&2; exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

# Single-instance per (project, self): claim a pidfile atomically (noclobber); break
# a stale claim only if its pid is dead; clean up only if it's still ours. Mirrors
# board-wait.sh so duplicate keepalives can't pile up.
SAFE_SELF="$(printf '%s' "$SELF" | tr -c 'A-Za-z0-9_.-' '_')"
PIDFILE="$BRIDGE_COLLAB/.keepalive_${SAFE_SELF}.pid"
while true; do
  if ( set -o noclobber; printf '%s\n' "$$" > "$PIDFILE" ) 2>/dev/null; then
    break
  fi
  OLDPID="$(cat "$PIDFILE" 2>/dev/null || echo "")"
  if bridge_pid_alive "$OLDPID"; then
    echo "[!] a presence-keepalive for '$SELF' is already running (pid $OLDPID) in $BRIDGE_COLLAB — not starting a duplicate." >&2
    exit 0
  fi
  rm -f "$PIDFILE"   # stale (holder dead) — retry the atomic claim
done
cleanup_pidfile() { [ "$(cat "$PIDFILE" 2>/dev/null || echo "")" = "$$" ] && rm -f "$PIDFILE"; }
trap cleanup_pidfile EXIT

# Driver loop: keep my last_seen fresh, then run idempotent safety checks.
while true; do
  "$PY3" "$HERE/_presence.py" heartbeat --self "$SELF" --project "$PROJECT" 2>/dev/null || true
  "$PY3" "$HERE/bridge-inbox.py" escalate --self "$SELF" --project "$PROJECT" >/dev/null 2>&1 || true
  "$PY3" "$HERE/_presence.py" departures --self "$SELF" --project "$PROJECT" >/dev/null 2>&1 || true
  sleep "$INTERVAL"
done
