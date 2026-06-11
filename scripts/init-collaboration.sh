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

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/.." && pwd)"
. "$HERE/bridge-paths.sh"

# Resolve the project ROOT: explicit arg, else git-root/cwd auto-location.
RAW_TARGET="${1:-$PWD}"
[ -d "$RAW_TARGET" ] || { echo "[x] target dir not found: $RAW_TARGET" >&2; exit 1; }
bridge_resolve "$RAW_TARGET"
ROOT="$BRIDGE_ROOT"
COLLAB="$ROOT/.collab"          # always create the .collab/ layout for new projects
mkdir -p "$COLLAB"

copy_if_absent() {
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then
    echo "[!] exists, leaving as-is: $dst"
  else
    cp "$src" "$dst"
    echo "[ok] created: $dst"
  fi
}

# Coordination layer -> <root>/.collab/
copy_if_absent "$REPO_DIR/templates/collaboration.md"          "$COLLAB/collaboration.md"
copy_if_absent "$REPO_DIR/templates/collaboration_signal.json" "$COLLAB/collaboration_signal.json"
copy_if_absent "$REPO_DIR/templates/collaboration_state.json"  "$COLLAB/collaboration_state.json"
copy_if_absent "$REPO_DIR/templates/collaboration_queue.json"  "$COLLAB/collaboration_queue.json"
# Auto-discovery hooks -> project ROOT (a fresh agent window auto-reads these).
# When initializing INTO ANOTHER project, rewrite the relative `scripts/...` refs
# to the ABSOLUTE bridge scripts dir (the scripts live in the bridge install, not
# the target project) so the printed commands actually resolve. In the bridge repo
# itself, relative paths already work, so leave them.
install_hook() {
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then echo "[!] exists, leaving as-is: $dst"; return; fi
  if [ "$ROOT" = "$REPO_DIR" ]; then
    cp "$src" "$dst"
  else
    sed "s#scripts/#$HERE/#g" "$src" > "$dst"
  fi
  echo "[ok] created: $dst"
}
install_hook "$REPO_DIR/AGENTS.md" "$ROOT/AGENTS.md"
install_hook "$REPO_DIR/CLAUDE.md" "$ROOT/CLAUDE.md"
TARGET="$COLLAB"   # the rest of this script's messages refer to the collab dir
echo "[==>] project root: $ROOT"
echo "[==>] coordination layer: $COLLAB"
[ "$ROOT" = "$REPO_DIR" ] || echo "[==>] bridge scripts: $HERE  (AGENTS.md/CLAUDE.md point here)"

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
        $HERE/watch-collaboration.sh --as claude --project "$ROOT"
        $HERE/watch-collaboration.sh --as codex  --project "$ROOT"
  - To start the loop, set status="active" and next_actor to whichever agent
    should move first in collaboration_state.json, then bump the signal.
  - Watch it: tail -f "$COLLAB/collaboration_auto.log"
  - SAFETY: read-only by default; pass --allow-write to let an agent edit project
    files. max_turns / max_cost in the state file cap the loop.

Multi-agent mode (--queue-mode: N agents per AI via a work queue):
  - Seed tasks in collaboration_queue.json (set control.status="active").
  - Start one watcher per agent with a distinct --agent-id:
        $HERE/watch-collaboration.sh --queue-mode --as codex  --agent-id codex-exec-1 --role executor --project "$ROOT"
        $HERE/watch-collaboration.sh --queue-mode --as codex  --agent-id codex-exec-2 --role executor --project "$ROOT"
        $HERE/watch-collaboration.sh --queue-mode --as claude --agent-id claude-rev   --role reviewer --project "$ROOT"
  - Agents claim eligible tasks in parallel (claim-under-lock + epoch fencing).
    Budget is control.max_turns in collaboration_queue.json.

Fill in the <placeholders> (roles, project state) to match your project.
EOF
