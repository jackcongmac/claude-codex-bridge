#!/usr/bin/env bash
#
# watch-collaboration.sh — autonomous-mode watcher for claude-codex-bridge.
#
# Watches collaboration_signal.json; on every change it runs ONE _auto_turn.py
# for this side. The watcher is deliberately dumb: all gating (whose turn,
# high-water dedupe, budget, CAS, halt) lives in _auto_turn.py. Idle cost is ~0
# (a file watcher or a stat poll — no LLM tokens until there is real work).
#
# Usage:
#   watch-collaboration.sh --as claude|codex [--project DIR] [--allow-write]
#                          [--max-turns N] [--max-cost USD] [--lock-ttl SECONDS]
#
# Start ONE per side: two shells on the SAME machine (or a shared filesystem) driving
# each agent — the board lives in local .collab/ and is NOT synced across machines, so
# two independent machines would each get their own disconnected board. Ctrl-C to stop.
# SAFETY: omit --allow-write for a read-only loop.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDE=""; PROJECT="$PWD"; ALLOW_WRITE=""; LOCK_TTL="600"
MAX_TURNS=""; MAX_COST=""; POLL_INTERVAL="${BRIDGE_POLL_INTERVAL:-2}"
QUEUE_MODE=""; AGENT_ID=""; ROLE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --as) SIDE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --allow-write) ALLOW_WRITE="--allow-write"; shift;;
    --max-turns) MAX_TURNS="$2"; shift 2;;
    --max-cost) MAX_COST="$2"; shift 2;;
    --lock-ttl) LOCK_TTL="$2"; shift 2;;
    --queue-mode) QUEUE_MODE="1"; shift;;
    --agent-id) AGENT_ID="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ "$SIDE" = "claude" ] || [ "$SIDE" = "codex" ] || { echo "[x] --as must be claude or codex" >&2; exit 2; }
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"; COLLAB="$BRIDGE_COLLAB"
SIGNAL="$COLLAB/collaboration_signal.json"
STATE="$COLLAB/collaboration_state.json"
PY3="$(command -v python3)"

if [ -n "$QUEUE_MODE" ]; then
  [ -n "$AGENT_ID" ] || { echo "[x] --queue-mode requires --agent-id <id>" >&2; exit 2; }
  [ -f "$COLLAB/collaboration_queue.json" ] || { echo "[x] no collaboration_queue.json — seed it (init-collaboration.sh)" >&2; exit 1; }
else
  [ -f "$STATE" ] || { echo "[x] no collaboration_state.json in $PROJECT — run scripts/init-collaboration.sh first" >&2; exit 1; }
fi

# Optionally patch budget caps into state at startup (convenience). Only when the
# loop is NOT active, so we never clobber an in-flight commit's state. (2-actor
# mode only; in --queue-mode the budget lives in collaboration_queue.json control.)
if [ -z "$QUEUE_MODE" ] && { [ -n "$MAX_TURNS" ] || [ -n "$MAX_COST" ]; }; then
  MAX_TURNS="$MAX_TURNS" MAX_COST="$MAX_COST" STATE="$STATE" "$PY3" - <<'PY'
import os, json
p=os.environ["STATE"]; s=json.load(open(p))
if s.get("status")=="active":
    print("[!] loop is active — not patching budget caps (edit %s while paused)" % p)
else:
    if os.environ.get("MAX_TURNS"): s["max_turns"]=int(os.environ["MAX_TURNS"])
    if os.environ.get("MAX_COST"):  s["max_cost_usd"]=float(os.environ["MAX_COST"])
    json.dump(s, open(p,"w"), ensure_ascii=False, indent=2)
PY
fi

run_turn() {
  # stdout (skip reasons) + stderr both go to the log so `tail -f` shows them.
  if [ -n "$QUEUE_MODE" ]; then
    "$PY3" "$HERE/_queue_turn.py" --as "$SIDE" --agent-id "$AGENT_ID" ${ROLE:+--role "$ROLE"} \
      --project "$PROJECT" --lock-ttl "$LOCK_TTL" $ALLOW_WRITE \
      >>"$COLLAB/collaboration_auto.log" 2>&1 || true
  else
    "$PY3" "$HERE/_auto_turn.py" --as "$SIDE" --project "$PROJECT" --lock-ttl "$LOCK_TTL" $ALLOW_WRITE \
      >>"$COLLAB/collaboration_auto.log" 2>&1 || true   # exit codes are advisory; harness logs
  fi
}

echo "[==>] watching $SIGNAL"
echo "      side=$SIDE  project=$PROJECT  allow_write=${ALLOW_WRITE:-no}  lock_ttl=${LOCK_TTL}s"
echo "      tail -f \"$COLLAB/collaboration_auto.log\" to watch the conversation."

# Run once at startup in case a turn is already pending for this side.
run_turn

# Slice 3b-min: background heartbeat — OBSERVE-ONLY stale-peer detection. It never
# covers automatically; it just logs `peer_stale_candidate` + notifies so a human
# can open coverage (Slice 3) if the peer's active turn has gone silent too long.
HEARTBEAT_INTERVAL="${BRIDGE_HEARTBEAT_INTERVAL:-60}"
if [ -z "$QUEUE_MODE" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  ( while true; do
      sleep "$HEARTBEAT_INTERVAL"
      "$PY3" "$HERE/_auto_turn.py" --as "$SIDE" --project "$PROJECT" --heartbeat \
        >>"$COLLAB/collaboration_auto.log" 2>&1 || true
    done ) &
  HB_PID=$!
  trap 'kill "$HB_PID" 2>/dev/null' EXIT
  echo "[==>] heartbeat every ${HEARTBEAT_INTERVAL}s (observe-only; notifies if the peer's active turn is stale > ${BRIDGE_STALE_SEC:-300}s)"
fi

if command -v fswatch >/dev/null 2>&1; then
  echo "[==>] using fswatch"
  # -l latency debounces bursts; each event triggers one turn (harness dedupes).
  fswatch -o -l 0.5 "$SIGNAL" | while read -r _; do run_turn; done
elif command -v inotifywait >/dev/null 2>&1; then
  echo "[==>] using inotifywait"
  while inotifywait -qq -e close_write -e moved_to "$(dirname "$SIGNAL")" >/dev/null 2>&1; do
    run_turn
  done
else
  echo "[==>] no fswatch/inotifywait — falling back to mtime polling every ${POLL_INTERVAL}s"
  echo "      (install fswatch for lower latency: brew install fswatch)"
  LAST=""
  while true; do
    CUR="$(stat -f %m "$SIGNAL" 2>/dev/null || stat -c %Y "$SIGNAL" 2>/dev/null || echo "")"
    if [ -n "$CUR" ] && [ "$CUR" != "$LAST" ]; then LAST="$CUR"; run_turn; fi
    sleep "$POLL_INTERVAL"
  done
fi
