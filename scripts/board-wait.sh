#!/usr/bin/env bash
#
# board-wait.sh — block until the OTHER agent updates the collaboration board.
#
# THE missing piece for interactive agents. A headless watcher
# (watch-collaboration.sh) auto-reacts to board changes, but an interactive
# agent (a Claude/Codex chat window) is request/response: it is dormant between
# turns and an external board write cannot wake it. This script bridges that gap:
# run it in the BACKGROUND from an interactive agent; it blocks until the peer
# bumps collaboration_signal.json (update_id changes AND updated_by != you), then
# prints what changed and EXITS. When a backgrounded process exits, the agent's
# harness re-invokes the agent — so the agent wakes and reads the board.
#
# Usage:
#   board-wait.sh --self <YourName> [--project DIR] [--since <update_id>]
#                 [--timeout SEC] [--interval SEC]
#
# Exit 0 + prints "CHANGED ..." + the changed section when the peer updates.
# Exit 0 + prints "TIMEOUT ..." when --timeout elapses (re-arm and wait again).
# The agent loop: run in background -> on wake, if CHANGED read+act+reply+re-arm;
# if TIMEOUT just re-arm. This is how an interactive agent becomes reactive.
set -euo pipefail

SELF=""; PROJECT="$PWD"; SINCE=""; TIMEOUT="${BRIDGE_BOARD_WAIT_TIMEOUT:-1800}"; INTERVAL="${BRIDGE_BOARD_WAIT_INTERVAL:-5}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --since) SINCE="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <YourName> required (the name to IGNORE so you don't wake on your own writes)" >&2; exit 2; }
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
SIGNAL="$BRIDGE_COLLAB/collaboration_signal.json"
PY3="$(command -v python3)"
[ -f "$SIGNAL" ] || { echo "[x] no $SIGNAL — run init-collaboration.sh first" >&2; exit 1; }

# Single-armer mutex per (project, self): a stale/duplicate armer running the
# presence scan with an out-of-date view is the main source of false-departure
# noise. If a live armer for this self already exists, don't start a duplicate.
PIDFILE="$BRIDGE_COLLAB/.boardwait_${SELF}.pid"
if [ -f "$PIDFILE" ]; then
  OLDPID="$(cat "$PIDFILE" 2>/dev/null || echo "")"
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    echo "[!] a board-wait for '$SELF' is already armed (pid $OLDPID) in $BRIDGE_COLLAB — not starting a duplicate." >&2
    exit 0
  fi
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

field() { _S="$SIGNAL" _K="$1" "$PY3" -c "import json,os;print(json.load(open(os.environ['_S'])).get(os.environ['_K'],''))" 2>/dev/null || echo ""; }

# Baseline: the update_id we start from (default = current).
[ -n "$SINCE" ] || SINCE="$(field update_id)"

DEADLINE=$(( $(date +%s) + TIMEOUT ))
while true; do
  # Presence tick: refresh my heartbeat + broadcast any peer whose window closed
  # (went stale). A broadcast bumps the signal, which the loop below then sees.
  DEP="$("$PY3" "$(dirname "${BASH_SOURCE[0]}")/_presence.py" tick --self "$SELF" --project "$PROJECT" 2>/dev/null || true)"
  if [ -n "$DEP" ]; then
    echo "$DEP"   # e.g. "DEPARTED codex-exec-2" — surface to the waking agent
  fi
  UID_NOW="$(field update_id)"; BY="$(field updated_by)"
  if [ -n "$UID_NOW" ] && [ "$UID_NOW" != "$SINCE" ] && [ "$BY" != "$SELF" ]; then
    SEC="$(field changed_section)"; SUM="$(field summary)"
    echo "CHANGED update_id=$UID_NOW by=$BY section=$SEC"
    echo "summary: $SUM"
    echo "--- '$SEC' section of collaboration.md ---"
    _P="$BRIDGE_COLLAB" _SEC="$SEC" "$PY3" -c "
import os
t=open(os.path.join(os.environ['_P'],'collaboration.md')).read()
h='## '+os.environ['_SEC']; i=t.find(h)
if i==-1: print('(section not found)')
else:
    nxt=t.find('\n## ', i+len(h)); print(t[i:(len(t) if nxt==-1 else nxt)].strip()[:2000])
" 2>/dev/null || true
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "TIMEOUT no peer update in ${TIMEOUT}s (since update_id=$SINCE) — re-arm to keep waiting."
    exit 0
  fi
  sleep "$INTERVAL"
done
