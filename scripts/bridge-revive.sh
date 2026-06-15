#!/usr/bin/env bash
#
# bridge-revive.sh — try to bring liveness back.
#
#   self  : re-ensure your own liveness (register + presence-keepalive) via bridge-live.
#   --peer: if the peer isn't live, post a board nudge (locked, via bridge-post) telling
#           them how to come back, and notify the human. NO MCP spawn — a throwaway peer
#           instance isn't the armed window; and we cannot reopen a closed window.
#
# Usage:
#   bridge-revive.sh --self <Me> [--project DIR] [--peer <Them>]
#                    [--keepalive-interval N] [--stale-after SEC]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/bridge-paths.sh"

SELF=""; PROJECT="$PWD"; PEER=""; KA_INT="${BRIDGE_KEEPALIVE_INTERVAL:-10}"
STALE_AFTER="${BRIDGE_PRESENCE_STALE:-1800}"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --peer) PEER="$2"; shift 2;;
    --keepalive-interval) KA_INT="$2"; shift 2;;
    --stale-after) STALE_AFTER="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$SELF" ] || { echo "[x] --self <Me> required" >&2; exit 2; }

bridge_resolve "$PROJECT"; PROJECT="$BRIDGE_ROOT"
PY3="$(command -v python3)"

# 1. SELF revive: re-ensure liveness (register + keepalive + report + board-wait line).
echo "[revive] self: re-ensuring liveness via bridge-live"
"$HERE/bridge-live.sh" --self "$SELF" --project "$PROJECT" --keepalive-interval "$KA_INT" || true

# 2. PEER revive (best-effort): nudge a peer that isn't live.
if [ -n "$PEER" ]; then
  # Quoted heredoc + values via env/argv (no shell expansion into Python source).
  PEER_VERDICT="$(_PY3="$PY3" _HERE="$HERE" _PROJECT="$PROJECT" _SA="$STALE_AFTER" \
    "$PY3" - "$PEER" <<'PYEOF'
import json, os, subprocess, sys
peer = sys.argv[1]
out = subprocess.run([os.environ["_PY3"], os.path.join(os.environ["_HERE"], "_liveness.py"),
                      "report", "--project", os.environ["_PROJECT"], "--json",
                      "--stale-after", os.environ["_SA"]],
                     capture_output=True, text=True).stdout
try:
    rows = json.loads(out)
except Exception:
    rows = []
print(next((r["verdict"] for r in rows if r.get("name") == peer), "MISSING"))
PYEOF
)"
  case "$PEER_VERDICT" in
    LIVE|PRESENT)
      echo "[revive] peer $PEER looks $PEER_VERDICT — nothing to revive." ;;
    *)
      echo "[revive] peer $PEER looks $PEER_VERDICT — posting a board nudge + notifying the human."
      MSG="@$PEER you look $PEER_VERDICT. To re-join: run $HERE/bridge-live.sh --self $PEER, THEN ARM the board-wait command it prints (run it in the background). If your window is CLOSED, a human must reopen it — I cannot."
      "$HERE/bridge-post.sh" --self "$SELF" --project "$PROJECT" --section "Liveness" \
        --message "$MSG" --summary "revive nudge to $PEER ($PEER_VERDICT)" \
        || echo "[!] could not post the nudge (lock busy?) — retry" >&2
      _HERE="$HERE" _MSG="revive: $PEER looks $PEER_VERDICT — nudged on the board" \
        "$PY3" -c 'import os, sys; sys.path.insert(0, os.environ["_HERE"]); import bridge_common as bc; bc.notify(os.environ["_MSG"])' 2>/dev/null || true ;;
  esac
fi
