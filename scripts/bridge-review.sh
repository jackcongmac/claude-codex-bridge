#!/usr/bin/env bash
#
# bridge-review.sh — record a peer review verdict bound to a commit SHA. This is what
# the bridge-push gate checks: a commit can't be pushed unless a PEER recorded a
# SHIP/GO for that SHA. Run this AS THE REVIEWER, after reviewing the author's commit.
#
# Usage:
#   bridge-review.sh --self <reviewer> --verdict SHIP|GO|FIX-FIRST|REVISE \
#                    [--sha <sha>] [--target "..."] [--note "..."] [--project DIR]
#   (--sha defaults to the repo's current HEAD)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"

SELF=""; PROJECT="$PWD"; SHA=""; VERDICT=""; TARGET=""; NOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --sha) SHA="$2"; shift 2;;
    --verdict) VERDICT="$2"; shift 2;;
    --target) TARGET="$2"; shift 2;;
    --note) NOTE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <reviewer> required" >&2; exit 2; }
[ -n "$VERDICT" ] || { echo "[x] --verdict required (SHIP/GO/FIX-FIRST/REVISE)" >&2; exit 2; }
[ -n "$SHA" ] || SHA="$(git -C "$PROJECT" rev-parse HEAD 2>/dev/null || true)"
[ -n "$SHA" ] || { echo "[x] --sha required (no git HEAD found at $PROJECT)" >&2; exit 2; }

bridge_resolve "$PROJECT"; PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

ARGS=(record --self "$SELF" --project "$PROJECT" --sha "$SHA" --verdict "$VERDICT")
[ -n "$TARGET" ] && ARGS+=(--target "$TARGET")
[ -n "$NOTE" ] && ARGS+=(--note "$NOTE")
"$PY3" "$HERE/_review.py" "${ARGS[@]}"
echo "[ok] recorded $VERDICT by '$SELF' for $SHA"
