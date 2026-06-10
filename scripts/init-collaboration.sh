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

copy_if_absent "$REPO_DIR/templates/collaboration.md"        "$TARGET/collaboration.md"
copy_if_absent "$REPO_DIR/templates/collaboration_signal.json" "$TARGET/collaboration_signal.json"

cat <<EOF

Coordination layer ready in: $TARGET

How the two agents use it:
  1. Both read collaboration_signal.json first; only re-read collaboration.md
     when update_id changes.
  2. Each writes status/findings to its own Outbox section in collaboration.md,
     then bumps collaboration_signal.json (update_id + summary).
  3. Use the MCP bridge (mcp__codex__codex / mcp__claude_chat__ask_claude) to
     poke the other agent to take a turn.

Fill in the <placeholders> (roles, project state) to match your project.
EOF
