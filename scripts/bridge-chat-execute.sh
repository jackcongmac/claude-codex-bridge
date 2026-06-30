#!/usr/bin/env bash
# Signal-driven supervisor for the chat-driven executor. OFF unless
# BRIDGE_CHAT_EXECUTE=1; while disabled, each pass is a cheap no-op.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$PWD"
INTERVAL="${BRIDGE_CHAT_EXECUTE_INTERVAL:-3}"
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    --*) echo "unknown arg: $1" >&2; exit 2;;
    *) PROJECT="$1"; shift;;
  esac
done

. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
SIGNAL="$BRIDGE_COLLAB/collaboration_signal.json"
PY3="$(command -v python3)"
[ -f "$SIGNAL" ] || { echo "[x] no $SIGNAL — run init-collaboration.sh first" >&2; exit 1; }

# Single-instance mutex per project: two execution supervisors could double-run
# the same greenlight. Claim atomically; break a stale claim only if its pid is dead.
PIDFILE="$BRIDGE_COLLAB/.chatexecute.pid"
while true; do
  if ( set -o noclobber; printf '%s\n' "$$" > "$PIDFILE" ) 2>/dev/null; then
    break
  fi
  OLDPID="$(cat "$PIDFILE" 2>/dev/null || echo "")"
  if bridge_pid_alive "$OLDPID"; then
    echo "[!] a chat execute supervisor is already running (pid $OLDPID) — not starting a duplicate." >&2
    exit 0
  fi
  rm -f "$PIDFILE"
done
cleanup_pidfile() {
  if [ "$(cat "$PIDFILE" 2>/dev/null || echo "")" = "$$" ]; then
    rm -f "$PIDFILE"
  fi
  return 0
}
trap cleanup_pidfile EXIT
trap 'cleanup_pidfile; exit 0' INT TERM

field() { _S="$SIGNAL" _K="$1" "$PY3" -c "import json,os;print(json.load(open(os.environ['_S'])).get(os.environ['_K'],''))" 2>/dev/null || echo ""; }

echo "[*] chat execute supervisor armed (project=$PROJECT, interval=${INTERVAL}s). Ctrl-C to stop."
LAST=""
while true; do
  UID_NOW="$(field update_id)"
  if [ -n "$UID_NOW" ] && [ "$UID_NOW" != "$LAST" ]; then
    if "$PY3" "$HERE/_chat_execute.py" once --project "$PROJECT" >/dev/null 2>&1; then
      LAST="$UID_NOW"
    fi
  fi
  sleep "$INTERVAL"
done
