#!/usr/bin/env bash
#
# claude-codex-bridge installer
# Wires up bidirectional MCP between Claude Code and Codex:
#   - Codex -> Claude : installs the ask_claude wrapper as a Codex MCP server
#   - Claude -> Codex : registers `codex mcp-server` as a user-scope Claude MCP server
#
# Idempotent: safe to re-run. Does not overwrite existing config blocks.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${CLAUDE_CODEX_BRIDGE_DIR:-$HOME/.claude-codex-bridge}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_CONFIG="$CODEX_HOME/config.toml"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. detect prerequisites -------------------------------------------------
PY3="$(command -v python3 || true)"
[ -n "$PY3" ] || die "python3 not found on PATH."

CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
[ -n "$CLAUDE_BIN" ] || die "Claude Code CLI (claude) not found. Install it or export CLAUDE_BIN=/path/to/claude."

CODEX_BIN="$(command -v codex || true)"
[ -n "$CODEX_BIN" ] || warn "Codex CLI (codex) not found on PATH. Claude->Codex registration will be skipped; install Codex and re-run, or run the claude mcp add manually."

say "python3:   $PY3"
say "claude:    $CLAUDE_BIN"
say "codex:     ${CODEX_BIN:-<not found>}"
say "codex home: $CODEX_HOME"

# --- 2. install the wrapper to a stable location -----------------------------
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/claude_chat_mcp.py" "$INSTALL_DIR/claude_chat_mcp.py"
say "installed wrapper -> $INSTALL_DIR/claude_chat_mcp.py"

# --- 3. register Codex -> Claude MCP server ----------------------------------
[ -f "$CODEX_CONFIG" ] || { mkdir -p "$CODEX_HOME"; touch "$CODEX_CONFIG"; }

if grep -q '^\[mcp_servers\.claude_chat\]' "$CODEX_CONFIG"; then
  warn "[mcp_servers.claude_chat] already present in $CODEX_CONFIG — leaving it as-is."
else
  cp "$CODEX_CONFIG" "$CODEX_CONFIG.bak_$(date +%Y%m%d_%H%M%S)"
  cat >> "$CODEX_CONFIG" <<EOF

[mcp_servers.claude_chat]
command = "$PY3"
args = ["$INSTALL_DIR/claude_chat_mcp.py"]
startup_timeout_sec = 30

[mcp_servers.claude_chat.env]
CLAUDE_BIN = "$CLAUDE_BIN"
EOF
  say "added [mcp_servers.claude_chat] to $CODEX_CONFIG (backup written)"
fi

# --- 4. register Claude -> Codex MCP server (user scope) ----------------------
if [ -n "$CODEX_BIN" ]; then
  if "$CLAUDE_BIN" mcp get codex >/dev/null 2>&1; then
    warn "Claude already has a 'codex' MCP server — leaving it as-is."
  else
    "$CLAUDE_BIN" mcp add codex -s user -- codex mcp-server
    say "registered 'codex' as a user-scope Claude MCP server"
  fi
fi

# --- 5. done -----------------------------------------------------------------
cat <<EOF

Done. Bidirectional bridge installed.

Next steps:
  1. RESTART Codex so it loads the new claude_chat MCP server.
  2. In Codex, talk to Claude:   call mcp__claude_chat__ask_claude  with a prompt
  3. In Claude, talk to Codex:   call mcp__codex__codex             with a prompt

The Claude colleague keeps memory per project directory automatically, and reads
./collaboration.md for context if present.
EOF
