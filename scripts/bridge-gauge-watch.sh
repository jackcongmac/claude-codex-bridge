#!/usr/bin/env bash
# bridge-gauge-watch.sh — live cross-model token gauge for an adjacent pane.
#
# Claude Code shows the gauge always-on via ccstatusline, but the Codex CLI has NO
# scriptable status line (verified on codex-cli 0.142.5: no statusline/footer/banner
# hook). So for symmetric always-on visibility next to Codex, run this in a tmux pane.
#
# Quick start (from inside your Codex tmux window):
#   scripts/bridge-gauge-watch.sh --split      # opens a 3-line pane below, auto-refresh
# Or wire it yourself:
#   tmux split-window -v -l 3 'scripts/bridge-gauge-watch.sh'
#
# Usage:
#   bridge-gauge-watch.sh [--interval N] [--once] [--split]
#     --interval N   refresh every N seconds (default 30; or env BRIDGE_GAUGE_INTERVAL)
#     --once         print the gauge once and exit (no loop)
#     --split        open a tmux split pane running this watcher (must be inside tmux)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAUGE="$HERE/bridge_usage.py"
INTERVAL="${BRIDGE_GAUGE_INTERVAL:-30}"
ONCE=0
SPLIT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --interval) [ $# -ge 2 ] || { echo "[x] --interval needs a value" >&2; exit 2; }; INTERVAL="$2"; shift 2;;
    --once) ONCE=1; shift;;
    --split) SPLIT=1; shift;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

case "$INTERVAL" in
  ''|*[!0-9]*) echo "[x] --interval must be a positive integer (got '$INTERVAL')" >&2; exit 2;;
esac
[ "$INTERVAL" -ge 1 ] || { echo "[x] --interval must be >= 1 second (got '$INTERVAL')" >&2; exit 2; }

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "[x] python3 not found on PATH" >&2; exit 1; }
[ -f "$GAUGE" ] || { echo "[x] gauge script not found: $GAUGE" >&2; exit 1; }

render() { "$PY" "$GAUGE" 2>/dev/null || echo "gauge unavailable"; }

if [ "$SPLIT" = "1" ]; then
  [ -n "${TMUX:-}" ] || { echo "[x] --split must be run inside a tmux session" >&2; exit 2; }
  tmux split-window -v -l 3 "'$HERE/bridge-gauge-watch.sh' --interval '$INTERVAL'"
  exit 0
fi

if [ "$ONCE" = "1" ]; then
  render
  exit 0
fi

# Live loop; Ctrl-C to stop.
trap 'exit 0' INT TERM
while :; do
  clear
  render
  sleep "$INTERVAL"
done
