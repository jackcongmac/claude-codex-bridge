#!/usr/bin/env bash
#
# compact-collaboration.sh — bounded-board archive rotation (token control).
#
# When collaboration.md grows past BRIDGE_BOARD_MAX_BYTES (default 64 KB), move
# old entries out of the log-like sections into collaboration_archive/, keeping
# the most recent BRIDGE_KEEP_ENTRIES (default 5) per section. Lossless, runs
# under the global lock, does NOT bump the signal (maintenance, not a turn).
#
# Usage:  scripts/compact-collaboration.sh [project_dir] [--keep N] [--force]
# Safe to run on a timer/cron. The pending section is never archived.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY3="$(command -v python3)"

# Optional leading non-flag positional = project dir; everything else passes through.
PROJECT_ARGS=()
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then PROJECT_ARGS=(--project "$1"); shift; fi

exec "$PY3" "$HERE/_compact.py" "${PROJECT_ARGS[@]}" "$@"
