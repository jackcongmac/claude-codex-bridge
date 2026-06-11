#!/usr/bin/env bash
#
# join-collaboration.sh — the JOIN handshake for any agent (new window or old).
#
# The North Star of this project is collaboration. A fresh agent window otherwise
# has no idea the board exists, what the protocol is, or that it must ARM. This
# script makes joining one step: it registers you in the participants list, prints
# the live rules + current board state, and tells you the exact ARM command to run
# in the background so you immediately become a reactive participant.
#
# Usage: scripts/join-collaboration.sh --self <YourName> [--role R] [--project DIR]
set -euo pipefail

SELF=""; ROLE="peer"; PROJECT="$PWD"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <YourName> required (your stable agent id this session)" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"
bridge_resolve "$PROJECT"
PROJECT="$BRIDGE_ROOT"; COLLAB="$BRIDGE_COLLAB"
PY3="$(command -v python3)"
BOARD="$COLLAB/collaboration.md"
SIGNAL="$COLLAB/collaboration_signal.json"

if [ ! -f "$BOARD" ] || [ ! -f "$SIGNAL" ]; then
  echo "[!] No board in $PROJECT yet. Create it first:"
  echo "      scripts/init-collaboration.sh \"$PROJECT\""
  exit 1
fi

# Register in participants.json (presence + role + last_seen), idempotent by name.
SELF="$SELF" ROLE="$ROLE" COLLAB="$COLLAB" "$PY3" - <<'PY'
import json, os, time
p = os.path.join(os.environ["COLLAB"], "collaboration_participants.json")
try:
    reg = json.load(open(p))
except Exception:
    reg = {"participants": []}
name, role = os.environ["SELF"], os.environ["ROLE"]
now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
found = False
for a in reg["participants"]:
    if a.get("name") == name:
        a["role"] = role; a["last_seen"] = now; a["departed"] = False; found = True
if not found:
    reg["participants"].append({"name": name, "role": role, "joined_at": now, "last_seen": now, "departed": False})
tmp = p + ".tmp"
json.dump(reg, open(tmp, "w"), ensure_ascii=False, indent=2)
os.replace(tmp, p)
PY

cat <<EOF

=== JOINED: $SELF (role=$ROLE) on the collaboration board ===
Project: $PROJECT

The rules you are now bound by (full: docs/agent-collaboration-protocol.md):
  1. STAY SYNCED — ARM board-wait in the background after every turn (below).
  2. Push only via  scripts/bridge-push.sh $SELF   (never bare git push).
  3. Respect file lanes; announce cross-lane edits on the board first.
  4. Review before merge: author+tests -> the OTHER AI reviews -> human approves.
  5. Coordinate releases on the board; check  gh release list  before tagging.

Current participants:
EOF
"$PY3" -c "
import json,os
r=json.load(open(os.path.join('$COLLAB','collaboration_participants.json')))
for a in r['participants']: print('  - %s (role=%s, last_seen=%s)'%(a['name'],a.get('role'),a.get('last_seen')))
"
echo ""
echo "Current board signal:"
"$PY3" -c "
import json
d=json.load(open('$SIGNAL'))
print('  update_id=%s updated_by=%s'%(d.get('update_id'),d.get('updated_by')))
print('  last summary:',d.get('summary'))
"
cat <<EOF

YOUR NEXT ACTIONS:
  1. Read the board to catch up:   cat "$BOARD"
  2. Announce yourself in your "## $SELF Outbox" + bump the signal.
  3. ARM (run in the BACKGROUND so you wake on peer updates):
       scripts/board-wait.sh --self "$SELF" --project "$PROJECT" &
  4. When board-wait exits: if CHANGED, act + reply + bump signal + re-ARM;
     if TIMEOUT, just re-ARM. Never treat silence as "peer is done" — re-ARM.
EOF
