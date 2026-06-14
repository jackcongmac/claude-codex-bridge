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
#                       [--message "first task to hand the peer on GO"]
#
# --message: on GO, also post this line to the board + bump the signal, so the
#   peer's board-wait wakes the peer WITH the first task in hand — closing the gap
#   between "channel confirmed" and "peer actually started". No-op on NO-GO.
#
# Exit 0 = GO (channel live). Non-zero = NO-GO (remediation printed). Never hangs
# past --timeout.
set -euo pipefail

SELF=""; PEER=""; PROJECT="$PWD"; TIMEOUT=""; INTERVAL="${BRIDGE_HANDSHAKE_INTERVAL:-1}"
CHECK_TRANSPORT=1; MESSAGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --peer) PEER="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    --message) MESSAGE="$2"; shift 2;;
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
  PEER_STATE="$(_P="$PARTICIPANTS" _PEER="$PEER" _STALE="$STALE" "$PY3" - <<'PY' 2>/dev/null || echo "missing|"
import json, os, time
from datetime import datetime
reg = json.load(open(os.environ["_P"]))
peer = os.environ["_PEER"]; stale = float(os.environ["_STALE"])
names = [a.get("name", "") for a in reg.get("participants", []) if a.get("name")]
match = next((a for a in reg.get("participants", []) if a.get("name") == peer), None)
if match is not None:
    if match.get("departed"):
        print("departed")
    else:
        ls = match.get("last_seen", "")
        try:
            t = time.mktime(datetime.strptime(ls.rsplit(" ", 1)[0], "%Y-%m-%d %H:%M:%S").timetuple())
            age = time.time() - t
            print("fresh %d" % age if age <= stale else "stale %d" % age)
        except Exception:
            print("present")
else:
    # G1: a case-only mismatch is the most common typo — point straight at the real name.
    ci = next((n for n in names if n.lower() == peer.lower()), None)
    if ci:
        print("suggest %s" % ci)
    else:
        print("missing|%s" % ",".join(names))
PY
)"
  case "$PEER_STATE" in
    fresh*) ok "peer $PEER joined, heartbeat fresh (${PEER_STATE#fresh })";;
    stale*) warn "peer $PEER joined but last_seen is OLD (${PEER_STATE#stale }s) — window may be closed; the live ping below is the real test";;
    present) ok "peer $PEER joined";;
    departed)
      bad "peer $PEER previously joined but is marked DEPARTED"
      addfix "if the $PEER window is CLOSED — open it, then in it:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer"
      addfix "if it's still OPEN — just re-join in it:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer   (then ARM board-wait)"
      FAIL=1;;
    suggest\ *)
      SUGG="${PEER_STATE#suggest }"
      bad "no peer named '$PEER' — but '$SUGG' is joined (names are case-sensitive)"
      addfix "retry with the exact name:  $HERE/bridge-handshake.sh --self \"$SELF\" --peer \"$SUGG\""
      FAIL=1;;
    missing*)
      JOINED="${PEER_STATE#missing}"; JOINED="${JOINED#|}"
      if [ -n "$JOINED" ]; then bad "peer $PEER has not joined this board (joined here: $JOINED)"
      else bad "peer $PEER has not joined — nobody has joined this board yet"; fi
      addfix "in the $PEER window:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer   then ARM board-wait"
      FAIL=1;;
    *) bad "peer $PEER has not joined this board"; addfix "in the $PEER window:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer   then ARM board-wait"; FAIL=1;;
  esac
else
  bad "no participants registered yet — nobody has joined this board"
  addfix "in the $PEER window:  $HERE/join-collaboration.sh --self \"$PEER\" --role peer   then ARM board-wait"
  FAIL=1
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
  # G5: optionally hand the first task across NOW, so the confirmed channel is used
  # immediately — peer's board-wait wakes the peer with this line in hand.
  if [ -n "$MESSAGE" ]; then
    if _HERE="$HERE" _PROJECT="$PROJECT" _SELF="$SELF" _PEER="$PEER" _MSG="$MESSAGE" "$PY3" - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["_HERE"])
from bridge_common import (collab_paths, append_to_outbox, read_json,
                           atomic_write_json, now_str, acquire_lock, release_lock)
p = collab_paths(os.environ["_PROJECT"])
self_name = os.environ["_SELF"]; peer = os.environ["_PEER"]; msg = os.environ["_MSG"]
# Take the SAME collaboration.lock every real board/signal write takes (see
# _presence.py / _auto_turn.py) — append + bump is a read-modify-write that would
# otherwise clobber a concurrent presence broadcast or auto-turn commit.
if not acquire_lock(p["lock"], "handshake-handoff-%d" % os.getpid(), ttl=30, wait=10):
    sys.exit(1)
try:
    section = "%s Outbox" % self_name
    append_to_outbox(p["board"], self_name, "**[handshake → %s]** %s" % (peer, msg))
    prev = int((read_json(p["signal"], default={}) or {}).get("update_id", 0))
    # Full signal schema (matches _presence.py): board-wait reads changed_section
    # to show the peer the section that changed — without it the peer wakes but
    # can't see WHAT changed.
    atomic_write_json(p["signal"], {
        "update_id": prev + 1, "updated_at": now_str(), "updated_by": self_name,
        "changed_section": section,
        "summary": ("handshake handoff to %s: %s" % (peer, msg))[:200],
    })
finally:
    release_lock(p["lock"])
PY
    then
      printf '   \033[1;32m↪\033[0m 已把第一条活儿发到板上并 bump signal —— %s 的 board-wait 会带着它醒来。\n' "$PEER"
    else
      warn "channel is live, but posting --message to the board failed; hand it off manually"
    fi
  fi
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
  echo   "   注意：直接 MCP 调用（mcp__codex__codex / ask_claude）会 spawn 一个全新的临时实例，"
  echo   "         它总会回应、但不在这块板上、也不是这个 armed 窗口——适合一次性提问，"
  echo   "         不能替代持续协作。要让*这个*窗口参与协作，仍需握手 GO。"
  exit 1
fi
