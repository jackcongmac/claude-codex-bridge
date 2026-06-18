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
  echo "      $HERE/init-collaboration.sh \"$PROJECT\""
  exit 1
fi

# Register in participants.json (presence + role + last_seen) — LOCKED + idempotent
# (a lock-free write could lose a concurrent join or clobber a presence/departure write).
if ! "$PY3" "$HERE/_presence.py" register --self "$SELF" --role "$ROLE" --project "$PROJECT"; then
  echo "[x] could not register on the board (collaboration lock busy?) — retry in a moment." >&2
  exit 1
fi

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
  2. Announce yourself (one locked step — appends to your outbox + bumps the signal):
       $HERE/bridge-post.sh --self "$SELF" --message "joined; <what you're working on>"
  3. GO LIVE in one command (starts/reuses presence-keepalive so your liveness
     stays honest, reports current liveness, and prints the board-wait ARM line):
       $HERE/bridge-live.sh --self "$SELF" --project "$PROJECT"
     then ARM board-wait in the BACKGROUND (its EXIT is how you wake on peer updates):
       $HERE/board-wait.sh --self "$SELF" --project "$PROJECT" &
  4. When board-wait exits: if CHANGED on the peer Outbox, inspect + receipt it:
       $HERE/bridge-inbox.sh pending --self "$SELF" --project "$PROJECT"
       $HERE/bridge-inbox.sh ack --self "$SELF" --project "$PROJECT" --status CLAIM --note "<what you will do>"
     Then act + reply via bridge-post.sh + re-ARM. TIMEOUT only happens if you
     explicitly pass --timeout; if it does, just re-ARM.
     Never treat silence as "peer is done" — verify liveness.
  5. Before handing real work to the peer, CONFIRM the channel is live:
       $HERE/bridge-handshake.sh --self "$SELF" --peer <Them>
     GO = both armed & listening; NO-GO prints the exact fix. Don't post into
     the void — silence from the peer is a failed handshake, not a dead channel.
  6. Staying current: this clone has no auto-update. Check for a newer version with
       $HERE/bridge-update.sh --check     (then  $HERE/bridge-update.sh  to pull it)
EOF
