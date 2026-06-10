#!/usr/bin/env bash
#
# Drop the coordination-layer templates into a project so two agents can
# collaborate via a shared board + low-token signal file.
#
# Usage:
#   scripts/init-collaboration.sh [target_dir]   # default: current directory
#
# Safe: never overwrites an existing collaboration.md / collaboration_signal.json.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$PWD}"

[ -d "$TARGET" ] || { echo "[x] target dir not found: $TARGET" >&2; exit 1; }

copy_if_absent() {
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then
    echo "[!] exists, leaving as-is: $dst"
  else
    cp "$src" "$dst"
    echo "[ok] created: $dst"
  fi
}

copy_if_absent "$REPO_DIR/templates/collaboration.md"          "$TARGET/collaboration.md"
copy_if_absent "$REPO_DIR/templates/collaboration_signal.json" "$TARGET/collaboration_signal.json"
copy_if_absent "$REPO_DIR/templates/collaboration_state.json"  "$TARGET/collaboration_state.json"

cat <<EOF

Coordination layer ready in: $TARGET

Manual mode (works today):
  1. Both read collaboration_signal.json first; only re-read collaboration.md
     when update_id changes.
  2. Each writes findings to its own Outbox in collaboration.md, then bumps
     collaboration_signal.json (update_id + summary).
  3. Use the MCP bridge (mcp__codex__codex / mcp__claude_chat__ask_claude) to
     poke the other agent to take a turn.

Autonomous mode (event-driven, no manual poke):
  - collaboration_state.json is the authoritative control state (starts paused).
  - Start a watcher per side; it auto-runs a turn when the other agent commits:
        scripts/watch-collaboration.sh --as claude --project "$TARGET"
        scripts/watch-collaboration.sh --as codex  --project "$TARGET"
  - To start the loop, set status="active" and next_actor to whichever agent
    should move first in collaboration_state.json, then bump the signal.
  - Watch it: tail -f "$TARGET/collaboration_auto.log"
  - SAFETY: read-only by default; pass --allow-write to let an agent edit project
    files. max_turns / max_cost in the state file cap the loop.

Fill in the <placeholders> (roles, project state) to match your project.
EOF
