#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY3="${PYTHON:-python3}"
exec "$PY3" "$HERE/_ship.py" "$@"
