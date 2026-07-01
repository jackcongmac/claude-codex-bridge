#!/usr/bin/env bash
#
# bridge-chat-respond.sh — run an agent's group-chat auto-responder.
#
# This is what makes the group chat REAL-TIME. Run it in the BACKGROUND once per
# agent (Claude, Codex). It watches collaboration_signal.json; whenever the board
# changes, it runs one responder pass: if ## Chat has an unhandled message for this
# agent — a human group message with no @ (everyone replies), or an explicit
# @this-agent / @All — it spawns the agent to write a brief reply and posts it back
# to ## Chat. On restart it scans older missed prompts and records handled message
# ids in .collab/chat_delivery.json, giving best-effort dedupe. Delivery is
# at-least-once: a crash after posting a reply but before recording handled state can
# re-answer that one message. An agent posting with no @ compels no one. Never replies
# to itself; a
# consecutive-agent-turn cap (default 6, --max-turns) breaks ping-pong until a
# human posts again.
#
# Usage:
#   bridge-chat-respond.sh --self <Claude|Codex> [--project DIR]
#                          [--interval SEC] [--max-turns N]
#
# Single-instance per (project, self). Ctrl-C / SIGTERM to stop.
set -euo pipefail

SELF=""; PROJECT="$PWD"; INTERVAL="${BRIDGE_CHAT_RESPOND_INTERVAL:-3}"; MAXT="${BRIDGE_CHAT_MAX_TURNS:-6}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    --max-turns) MAXT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <Claude|Codex> required" >&2; exit 2; }

# Running this wrapper IS the explicit opt-in to auto-responding (the responder is
# off by default to avoid read-only/writable identity confusion; see
# _chat_respond.py). Allow callers to override the environment.
export BRIDGE_CHAT_AUTORESPOND="${BRIDGE_CHAT_AUTORESPOND:-1}"

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"
SIGNAL="$BRIDGE_COLLAB/collaboration_signal.json"
PY3="$(command -v python3)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SIGNAL" ] || { echo "[x] no $SIGNAL — run init-collaboration.sh first" >&2; exit 1; }

# Single-instance mutex per (project, self): two responders for the same agent would
# double-reply. Claim atomically; break a stale claim only if its pid is dead.
SAFE_SELF="$(printf '%s' "$SELF" | tr -c 'A-Za-z0-9_.-' '_')"
PIDFILE="$BRIDGE_COLLAB/.chatrespond_${SAFE_SELF}.pid"
while true; do
  if ( set -o noclobber; printf '%s\n' "$$" > "$PIDFILE" ) 2>/dev/null; then
    break
  fi
  OLDPID="$(cat "$PIDFILE" 2>/dev/null || echo "")"
  if bridge_pid_alive "$OLDPID"; then
    echo "[!] a chat responder for '$SELF' is already running (pid $OLDPID) — not starting a duplicate." >&2
    exit 0
  fi
  rm -f "$PIDFILE"
done
cleanup_pidfile() { [ "$(cat "$PIDFILE" 2>/dev/null || echo "")" = "$$" ] && rm -f "$PIDFILE"; }
trap cleanup_pidfile EXIT INT TERM

field() { _S="$SIGNAL" _K="$1" "$PY3" -c "import json,os;print(json.load(open(os.environ['_S'])).get(os.environ['_K'],''))" 2>/dev/null || echo ""; }

echo "[*] chat responder for '$SELF' armed (project=$PROJECT, interval=${INTERVAL}s, max-turns=$MAXT). Ctrl-C to stop."
# React to the current tail once on startup (the @ may already be waiting). LAST
# stays UNSET until a pass succeeds, so a startup lock-busy (exit 3) leaves LAST
# empty and the loop immediately retries (UID_NOW != "").
START_UID="$(field update_id)"
LAST=""
"$PY3" "$HERE/_chat_respond.py" once --self "$SELF" --project "$PROJECT" --max-turns "$MAXT" >/dev/null 2>&1 && LAST="$START_UID" || true
while true; do
  UID_NOW="$(field update_id)"
  if [ -n "$UID_NOW" ] && [ "$UID_NOW" != "$LAST" ]; then
    # Advance LAST only on success (exit 0). respond_once already drops a reply that
    # was made stale by an interleaved message (returns "superseded", exit 0) and
    # exits 3 only on a transient lock-busy — which leaves UID_NOW != LAST so the
    # next loop retries. A successful reply bumps the signal; next loop re-fires and
    # the pass sees its own message → cheap "self" no-op, then settles.
    if "$PY3" "$HERE/_chat_respond.py" once --self "$SELF" --project "$PROJECT" --max-turns "$MAXT" >/dev/null 2>&1; then
      LAST="$UID_NOW"
    fi
  fi
  sleep "$INTERVAL"
done
