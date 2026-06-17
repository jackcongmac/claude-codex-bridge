#!/usr/bin/env bash
#
# bridge-chat.sh — a shared group-chat thread (## Chat) for human + Claude + Codex.
#
# Everyone posts to ONE chronological thread (locked, via bridge-post), prefixed with
# the speaker, so all three read the same conversation. Armed agents' board-wait wakes
# them on each new message. An MVP "discuss together" room — text only for now.
#
# Usage:
#   bridge-chat.sh --self <Me> --message "…"    # post a line to the chat
#   bridge-chat.sh                              # print the thread once
#   bridge-chat.sh --watch [--interval N]       # live-tail the thread
#   bridge-chat.sh --self <Me> --interactive    # terminal chat; Esc exits
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"

SELF=""; PROJECT="$PWD"; MESSAGE=""; WATCH=0; INTERACTIVE=0; NO_RESPONDERS=0
INTERVAL="${BRIDGE_CHAT_INTERVAL:-2}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --message) MESSAGE="$2"; shift 2;;
    --watch) WATCH=1; shift;;
    --interactive) INTERACTIVE=1; shift;;
    --no-responders) NO_RESPONDERS=1; shift;;
    --interval) INTERVAL="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

bridge_resolve "$PROJECT"; PROJECT="$BRIDGE_ROOT"; COLLAB="$BRIDGE_COLLAB"
PY3="$(command -v python3)"
BOARD="$COLLAB/collaboration.md"
SIGNAL="$COLLAB/collaboration_signal.json"

if [ "$INTERACTIVE" = "1" ]; then
  [ -n "$SELF" ] || { echo "[x] --self <Me> required for --interactive" >&2; exit 2; }
  ARGS=(--self "$SELF" --project "$PROJECT" --interval "$INTERVAL")
  [ "$NO_RESPONDERS" = "1" ] && ARGS+=(--no-responders)
  exec "$PY3" "$HERE/bridge-chat-tui.py" "${ARGS[@]}"
fi

# POST a line — delegate to the locked transactional write, under the shared ## Chat
# section, prefixed with the speaker so the thread reads like a chat.
if [ -n "$MESSAGE" ]; then
  [ -n "$SELF" ] || { echo "[x] --self <Me> required to post" >&2; exit 2; }
  FORMATTED="$(_HERE="$HERE" _SELF="$SELF" _MESSAGE="$MESSAGE" "$PY3" - <<'PY'
import importlib.util
import os

path = os.path.join(os.environ["_HERE"], "bridge-chat-web.py")
spec = importlib.util.spec_from_file_location("chatweb", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.format_chat_message(os.environ["_SELF"], os.environ["_MESSAGE"]), end="")
PY
)"
  exec "$HERE/bridge-post.sh" --self "$SELF" --project "$PROJECT" --section "Chat" \
    --message "$FORMATTED" --summary "chat: $SELF: $MESSAGE"
fi

print_chat() {
  _HERE="$HERE" _BOARD="$BOARD" "$PY3" - <<'PY'
import importlib.util
import os
import sys
sys.path.insert(0, os.environ["_HERE"])
from bridge_common import read_section

path = os.path.join(os.environ["_HERE"], "bridge-chat-web.py")
spec = importlib.util.spec_from_file_location("chatweb", path)
chatweb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chatweb)

sec = read_section(os.environ["_BOARD"], "Chat")
msgs = chatweb.parse_chat(sec)
if not msgs:
    print('(no messages yet — post with: bridge-chat.sh --self <Me> --message "…")')
else:
    print("## Chat\n")
    for msg in msgs:
        print("### %s\n" % msg["ts"])
        print("**%s:** %s\n" % (msg["speaker"], msg["text"]))
PY
}

if [ "$WATCH" = "1" ]; then
  last=""
  while :; do
    uid="$(_S="$SIGNAL" "$PY3" -c "import json,os;print(json.load(open(os.environ['_S'])).get('update_id',''))" 2>/dev/null || echo "")"
    if [ "$uid" != "$last" ]; then
      clear 2>/dev/null || true
      echo "=== Chat — $COLLAB  (Ctrl-C to leave) ==="
      print_chat
      last="$uid"
    fi
    sleep "$INTERVAL"
  done
else
  print_chat
fi
