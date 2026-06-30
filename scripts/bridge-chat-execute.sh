#!/usr/bin/env bash
# One pass of the chat-driven executor. OFF unless BRIDGE_CHAT_EXECUTE=1.
# Host it the same way as bridge-chat-respond.sh (supervised loop) when wiring P0a end-to-end.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-$PWD}"
exec python3 "$HERE/_chat_execute.py" once --project "$PROJECT"
