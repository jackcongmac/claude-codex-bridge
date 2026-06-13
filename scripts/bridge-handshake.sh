#!/usr/bin/env bash
#
# bridge-handshake.sh — prove the collaboration channel is LIVE before you hand off.
#
# THE fix for "就尬在那里": you tell the two agents to contact each other, one side
# isn't actually ready (Codex not restarted, peer not joined, board-wait not ARMed),
# and the board post / blocking MCP call hangs in silence with no diagnosis.
#
# This runs a FAST preflight that NEVER hangs (hard timeout), then either prints a
# friendly GO confirmation (so the user can relax) or a NO-GO with the EXACT fix
# command — instead of silence.
#
# It does two things:
#   1. Static checks (instant, local): board found, I'M armed, peer present + fresh,
#      transport wired.
#   2. A live liveness ping: writes a ping addressed to the peer, then waits for the
#      peer's board-wait.sh to pong it (harness-layer ACK). A pong proves the peer's
#      ARM mechanism — the thing real collaboration depends on — is actually alive.
#
# Usage:
#   bridge-handshake.sh --self <Me> --peer <Them> [--project DIR]
#                       [--timeout SEC] [--interval SEC] [--no-transport]
#
# Exit 0 = GO (channel live). Non-zero = NO-GO (remediation printed). Never hangs
# past --timeout.
set -euo pipefail

SELF=""; PEER=""; PROJECT="$PWD"; TIMEOUT=""; INTERVAL="${BRIDGE_HANDSHAKE_INTERVAL:-1}"
CHECK_TRANSPORT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --peer) PEER="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
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
PY3="$(command -v python3)"
SIGNAL="$COLLAB/collaboration_signal.json"
PARTICIPANTS="$COLLAB/collaboration_participants.json"
STALE="${BRIDGE_PRESENCE_STALE:-1800}"

# Default timeout: long enough for a board-wait on its poll interval to catch + ACK.
# Codex: max(15, 3*interval+1). board-wait's default interval is 5s.
if [ -z "$TIMEOUT" ]; then
  PEER_INT="${BRIDGE_BOARD_WAIT_INTERVAL:-5}"
  TIMEOUT="$("$PY3" -c "print(max(15, int(3*float('$PEER_INT'))+1))")"
fi

ok()   { printf '   \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '   \033[1;31m✗\033[0m %s\n' "$*"; }
warn() { printf '   \033[1;33m!\033[0m %s\n' "$*"; }

FAIL=0
FIXES=""
addfix() { FIXES="$FIXES   - $1\n"; }

echo "握手预检 (handshake preflight): $SELF → $PEER"
echo "Project: $PROJECT"
echo "Static checks:"

# 1. Board present
if [ -f "$SIGNAL" ]; then
  UID_NOW="$(_S="$SIGNAL" "$PY3" -c "import json,os;print(json.load(open(os.environ['_S'])).get('update_id',''))" 2>/dev/null || echo "")"
  ok "board found (signal update_id=$UID_NOW)"
else
  bad "no board at $COLLAB"
  addfix "create it:  $HERE/init-collaboration.sh \"$PROJECT\""
  FAIL=1
fi

# 2. I am ARMed (my own board-wait pidfile exists + pid alive)
SAFE_SELF="$(printf '%s' "$SELF" | tr -c 'A-Za-z0-9_.-' '_')"
MY_PID_FILE="$COLLAB/.boardwait_${SAFE_SELF}.pid"
MY_PID="$(cat "$MY_PID_FILE" 2>/dev/null || echo "")"
if [ -n "$MY_PID" ] && kill -0 "$MY_PID" 2>/dev/null; then
  ok "I ($SELF) am ARMed (board-wait pid $MY_PID)"
else
  bad "I ($SELF) am NOT ARMed — I won't react to the peer either"
  addfix "ARM yourself:  $HERE/board-wait.sh --self \"$SELF\" --project \"$PROJECT\" &"
  FAIL=1
fi

# 3. Peer present, not departed, fresh last_seen
if [ -f "$PARTICIPANTS" ]; then
  PEER_STATE="$(_P="$PARTICIPANTS" _PEER="$PEER" _STALE="$STALE" "$PY3" - <<'PY' 2>/dev/null || echo "missing"
import json, os, time
from datetime import datetime
reg = json.load(open(os.environ["_P"]))
peer = os.environ["_PEER"]; stale = float(os.environ["_STALE"])
for a in reg.get("participants", []):
    if a.get("name") == peer:
        if a.get("departed"):
            print("departed"); break
        ls = a.get("last_seen", "")
        try:
            t = time.mktime(datetime.strptime(ls.rsplit(" ", 1)[0], "%Y-%m-%d %H:%M:%S").timetuple())
            age = time.time() - t
            print("fresh %d" % age if age <= stale else "stale %d" % age)
        except Exception:
            print("present")
        break
else:
    print("missing")
PY
)"
  case "$PEER_STATE" in
    fresh*) ok "peer $PEER joined, heartbeat fresh (${PEER_STATE#fresh })";;
    stale*) warn "peer $PEER joined but last_seen is OLD (${PEER_STATE#stale }s) — window may be closed; the live ping below is the real test";;
    present) ok "peer $PEER joined";;
    departed) bad "peer $PEER marked DEPARTED (window closed)"; addfix "re-join in the $PEER window:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer"; FAIL=1;;
    *) bad "peer $PEER has not joined this board"; addfix "in the $PEER window:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer  then ARM board-wait"; FAIL=1;;
  esac
fi

# 4. Transport wired (best-effort)
if [ "$CHECK_TRANSPORT" = "1" ]; then
  if command -v claude >/dev/null 2>&1; then
    if claude mcp get codex >/dev/null 2>&1; then ok "transport: codex MCP registered"
    else warn "transport: 'codex' MCP not registered for Claude — re-run ./install.sh, restart Codex"; fi
  fi
  CODEX_CFG="${CODEX_HOME:-$HOME/.codex}/config.toml"
  if [ -f "$CODEX_CFG" ] && grep -q '^\[mcp_servers\.claude_chat\]' "$CODEX_CFG" 2>/dev/null; then
    ok "transport: claude_chat in Codex config"
  else
    warn "transport: [mcp_servers.claude_chat] missing from $CODEX_CFG — re-run ./install.sh, restart Codex"
  fi
fi

if [ "$FAIL" = "1" ]; then
  echo ""
  printf '\033[1;31m❌ 握手失败 — 先别开始，差一步（这不是卡住，是对面没就绪）\033[0m\n'
  printf "修复：\n$FIXES"
  echo "   修好后重跑：$HERE/bridge-handshake.sh --self \"$SELF\" --peer \"$PEER\""
  exit 1
fi

# 5. Live ping — the real test that the peer's board-wait is ARMed and alive.
echo ""
echo "Live ping → waiting up to ${TIMEOUT}s for $PEER's board-wait to pong (this is expected to take a few seconds, NOT a hang)…"
NONCE="$("$PY3" "$HERE/_handshake.py" ping --self "$SELF" --peer "$PEER" --project "$PROJECT" --ttl "$TIMEOUT")" || {
  bad "could not write handshake ping (lock contention?)"; exit 1; }
if RTT="$("$PY3" "$HERE/_handshake.py" poll --self "$SELF" --peer "$PEER" --nonce "$NONCE" --project "$PROJECT" --timeout "$TIMEOUT" --interval "$INTERVAL")"; then
  echo ""
  printf '\033[1;32m✅ 握手成功 — 可以放心开始协作\033[0m\n'
  printf '   Peer:       %s — 在线，board-wait 已 ARM\n' "$PEER"
  printf '   Board:      %s   (signal update_id=%s)\n' "$COLLAB" "${UID_NOW:-?}"
  printf '   %s\n' "${RTT/PONG /Round-trip: }s"
  printf '   两个 agent 现在都在监听同一块板。开始。\n'
  exit 0
else
  echo ""
  printf '\033[1;31m❌ 握手失败 — peer 没有响应（这不是卡住）\033[0m\n'
  printf '   %s 在 %ss 内没有回应握手 ping。\n' "$PEER" "$TIMEOUT"
  printf '   最可能：%s 窗口没有 ARM board-wait（它不会自动对板更新做反应）。\n' "$PEER"
  echo   "   修复 — 在 $PEER 窗口里运行（两个窗口都要 ARM）："
  echo   "     $HERE/join-collaboration.sh --self \"$PEER\" --role peer"
  echo   "     $HERE/board-wait.sh --self \"$PEER\" --project \"$PROJECT\" &"
  echo   "   修好后重跑：$HERE/bridge-handshake.sh --self \"$SELF\" --peer \"$PEER\""
  exit 1
fi
