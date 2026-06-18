#!/usr/bin/env bash
# Sourced helper: resolve the project root + coordination-layer dir for bash
# scripts, using the SAME logic as bridge_common.find_project_root / collab_paths
# (single source of truth). Call `bridge_resolve [project-dir]` (empty = auto from
# cwd); it sets BRIDGE_ROOT and BRIDGE_COLLAB.
bridge_resolve() {
  local proj="${1:-}"
  local here py
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  py="$(command -v python3)"
  BRIDGE_ROOT="$("$py" -c "import sys;sys.path.insert(0,'$here');import bridge_common as bc;print(bc.find_project_root('$proj'))")"
  BRIDGE_COLLAB="$("$py" -c "import sys;sys.path.insert(0,'$here');import bridge_common as bc;print(bc.collab_paths('$BRIDGE_ROOT')['dir'])")"
}

bridge_pid_alive() {
  local pid="${1:-}"
  local here py
  case "$pid" in
    ""|*[!0-9]*) return 1;;
  esac
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  py="$(command -v python3)"
  "$py" - "$here" "$pid" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from bridge_common import _pid_alive

sys.exit(0 if _pid_alive(int(sys.argv[2])) else 1)
PY
}
