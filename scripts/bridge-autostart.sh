#!/usr/bin/env bash
#
# bridge-autostart.sh — proactive activation handshake for a collaboration session.
#
# Run this after the agent has armed board-wait using its harness-native background
# mechanism. This joins the board, goes live, then proves the peer channel with
# bridge-handshake. A NO-GO is reported clearly and leaves a board invite, but is
# intentionally non-blocking: the agent should keep doing work that does not
# require the peer.
set -euo pipefail

SELF=""; PEER=""; ROLE="peer"; PROJECT="$PWD"
KEEPALIVE_INTERVAL="${BRIDGE_KEEPALIVE_INTERVAL:-10}"
HANDSHAKE_TIMEOUT=""
HANDSHAKE_INTERVAL="${BRIDGE_HANDSHAKE_INTERVAL:-1}"
CHECK_TRANSPORT=1

while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --peer) PEER="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --keepalive-interval) KEEPALIVE_INTERVAL="$2"; shift 2;;
    --handshake-timeout) HANDSHAKE_TIMEOUT="$2"; shift 2;;
    --handshake-interval) HANDSHAKE_INTERVAL="$2"; shift 2;;
    --no-transport) CHECK_TRANSPORT=0; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$SELF" ] || { echo "[x] --self <Me> required" >&2; exit 2; }
[ -n "$PEER" ] || { echo "[x] --peer <Them> required" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"; COLLAB="$BRIDGE_COLLAB"

echo "[autostart] join: $SELF role=$ROLE project=$PROJECT"
"$HERE/join-collaboration.sh" --self "$SELF" --role "$ROLE" --project "$PROJECT" >/dev/null

echo "[autostart] go-live: presence keepalive + liveness report"
"$HERE/bridge-live.sh" --self "$SELF" --role "$ROLE" --project "$PROJECT" \
  --keepalive-interval "$KEEPALIVE_INTERVAL"

cat <<EOF
[autostart] board-wait must already be armed by the agent harness.
If handshake reports that $SELF is not armed, run this as a harness-tracked background task, then rerun autostart:
  $HERE/board-wait.sh --self "$SELF" --project "$PROJECT" &
EOF

HS=("$HERE/bridge-handshake.sh" --self "$SELF" --peer "$PEER" --project "$PROJECT" \
    --interval "$HANDSHAKE_INTERVAL")
[ -z "$HANDSHAKE_TIMEOUT" ] || HS+=(--timeout "$HANDSHAKE_TIMEOUT")
[ "$CHECK_TRANSPORT" = "1" ] || HS+=(--no-transport)

echo "[autostart] handshake: $SELF -> $PEER"
set +e
HS_OUT="$("${HS[@]}" 2>&1)"
HS_STATUS=$?
set -e
printf '%s\n' "$HS_OUT"

if [ "$HS_STATUS" -eq 0 ]; then
  echo "✅ channel LIVE — activation handshake succeeded."
  exit 0
fi

if printf '%s\n' "$HS_OUT" | grep -F "I ($SELF) am NOT ARMed" >/dev/null 2>&1; then
  cat <<EOF
NON-BLOCKING NO-GO — $SELF is not harness-ARMed yet, so no peer invite was posted.
Self fix:
  $HERE/board-wait.sh --self "$SELF" --project "$PROJECT" &
EOF
  exit "$HS_STATUS"
fi

FIX_JOIN="$HERE/join-collaboration.sh --self \"$PEER\" --role peer --project \"$PROJECT\""
FIX_ARM="$HERE/board-wait.sh --self \"$PEER\" --project \"$PROJECT\" &"
INVITE="$SELF online. @$PEER please join+ARM to handshake back. Fix: $FIX_JOIN ; $FIX_ARM"
"$HERE/bridge-post.sh" --self "$SELF" --project "$PROJECT" \
  --message "$INVITE" \
  --summary "autostart NO-GO invite to $PEER" >/dev/null 2>&1 || true

cat <<EOF
NON-BLOCKING NO-GO — proceed with work that does not require $PEER, but do not hand off until handshake GO.
Peer fix:
  $FIX_JOIN
  $FIX_ARM
EOF
exit "$HS_STATUS"
