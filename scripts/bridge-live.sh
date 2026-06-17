#!/usr/bin/env bash
#
# bridge-live.sh — one-command "go live" for a collaboration participant.
#
# It registers the agent, starts the presence keepalive if it is not already
# running, prints current liveness, and gives the exact board-wait ARM command.
# The default ARM command preserves wake-on-exit: board-wait exits on CHANGED/TIMEOUT
# so the agent harness can wake the interactive pane. --stay-armed is an optional
# liveness/pong helper, not the default wake task.
set -euo pipefail

SELF=""; ROLE="peer"; PROJECT="$PWD"; KEEPALIVE_INTERVAL="${BRIDGE_KEEPALIVE_INTERVAL:-10}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --keepalive-interval) KEEPALIVE_INTERVAL="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <Me> required" >&2; exit 2; }
if ! awk -v x="$KEEPALIVE_INTERVAL" 'BEGIN{exit !(x>0)}' 2>/dev/null; then
  echo "[x] --keepalive-interval must be a positive number (got '$KEEPALIVE_INTERVAL')" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"; COLLAB="$BRIDGE_COLLAB"
PY3="$(command -v python3)"

if [ ! -f "$COLLAB/collaboration.md" ] || [ ! -f "$COLLAB/collaboration_signal.json" ]; then
  echo "[!] No board in $PROJECT yet. Create it first:"
  echo "      $HERE/init-collaboration.sh \"$PROJECT\""
  exit 1
fi

if ! "$PY3" "$HERE/_presence.py" register --self "$SELF" --role "$ROLE" --project "$PROJECT"; then
  echo "[x] could not register on the board (collaboration lock busy?) — retry in a moment." >&2
  exit 1
fi

SAFE_SELF="$(printf '%s' "$SELF" | tr -c 'A-Za-z0-9_.-' '_')"
PIDFILE="$COLLAB/.keepalive_${SAFE_SELF}.pid"
if [ -f "$PIDFILE" ]; then
  OLDPID="$(cat "$PIDFILE" 2>/dev/null || echo "")"
else
  OLDPID=""
fi

if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
  echo "presence-keepalive already running for $SELF (pid $OLDPID)"
else
  "$HERE/presence-keepalive.sh" --self "$SELF" --project "$PROJECT" \
    --interval "$KEEPALIVE_INTERVAL" >/dev/null 2>&1 &
  echo "started presence-keepalive for $SELF"
fi

# Give the background process a brief chance to claim its pidfile before reporting.
for _ in 1 2 3 4 5; do
  [ -s "$PIDFILE" ] && break
  sleep 0.1
done

"$HERE/bridge-liveness.sh" report --self "$SELF" --project "$PROJECT"

cat <<EOF

ARM board-wait in the background after this turn:
  $HERE/board-wait.sh --self "$SELF" --project "$PROJECT" &
EOF
