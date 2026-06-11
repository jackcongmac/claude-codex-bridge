#!/usr/bin/env bash
#
# claude-codex-bridge installer
# Wires up bidirectional MCP between Claude Code and Codex:
#   - Codex -> Claude : installs the ask_claude wrapper as a Codex MCP server
#   - Claude -> Codex : registers `codex mcp-server` as a user-scope Claude MCP server
#
# Idempotent: safe to re-run. Does not overwrite existing config blocks.
#
# Options (env vars):
#   CLAUDE_BIN=/path/to/claude     override Claude CLI detection
#   BRIDGE_READONLY=1              install the Claude colleague as READ-ONLY
#                                  (Read/Grep/Glob only; no Edit/Write)
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
[ -n "$CODEX_BIN" ] || warn "Codex CLI (codex) not found on PATH. Claude->Codex registration will be skipped; install Codex and re-run."

say "python3:    $PY3"
say "claude:     $CLAUDE_BIN"
say "codex:      ${CODEX_BIN:-<not found>}"
say "codex home: $CODEX_HOME"

# --- 2. security posture -----------------------------------------------------
if [ "${BRIDGE_READONLY:-0}" = "1" ]; then
  ALLOWED_TOOLS="Read Grep Glob"
  say "security: READ-ONLY colleague (Read/Grep/Glob only)."
else
  ALLOWED_TOOLS="Read Grep Glob Edit Write TodoWrite"
  warn "SECURITY: the Claude colleague will be able to READ *and EDIT/WRITE* files"
  warn "          in whatever directory Codex calls it from, using your Claude"
  warn "          credentials. Re-run with BRIDGE_READONLY=1 for a read-only setup."
fi

# --- 3. install the wrapper to a stable location -----------------------------
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/claude_chat_mcp.py" "$INSTALL_DIR/claude_chat_mcp.py"
say "installed wrapper -> $INSTALL_DIR/claude_chat_mcp.py"

# --- 4. register Codex -> Claude MCP server (TOML-safe) -----------------------
[ -f "$CODEX_CONFIG" ] || { mkdir -p "$CODEX_HOME"; touch "$CODEX_CONFIG"; }

# All string values are escaped via Python's json.dumps, which emits valid TOML
# basic strings (handles spaces, backslashes, quotes, e.g. Windows paths).
PY3="$PY3" CLAUDE_BIN="$CLAUDE_BIN" INSTALL_DIR="$INSTALL_DIR" \
ALLOWED_TOOLS="$ALLOWED_TOOLS" CODEX_CONFIG="$CODEX_CONFIG" \
"$PY3" - <<'PYEOF'
import os, json, re, shutil, datetime

cfg = os.environ["CODEX_CONFIG"]
with open(cfg) as f:
    text = f.read()

if re.search(r'(?m)^\[mcp_servers\.claude_chat\]', text):
    print("[!] [mcp_servers.claude_chat] already present in config.toml - leaving as-is.")
    print("    (BRIDGE_READONLY / tool-allowlist changes only apply when the block is first created;")
    print("     edit CLAUDE_CHAT_ALLOWED_TOOLS in that block, or remove it and re-run, to change perms.)")
else:
    shutil.copy(cfg, cfg + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    def s(v):  # JSON strings are valid TOML basic strings for our values
        return json.dumps(v)
    args = [os.path.join(os.environ["INSTALL_DIR"], "claude_chat_mcp.py")]
    block = (
        "\n[mcp_servers.claude_chat]\n"
        "command = " + s(os.environ["PY3"]) + "\n"
        "args = [" + ", ".join(s(a) for a in args) + "]\n"
        "startup_timeout_sec = 30\n\n"
        "[mcp_servers.claude_chat.env]\n"
        "CLAUDE_BIN = " + s(os.environ["CLAUDE_BIN"]) + "\n"
        "CLAUDE_CHAT_ALLOWED_TOOLS = " + s(os.environ["ALLOWED_TOOLS"]) + "\n"
    )
    if not text.endswith("\n") and text:
        text += "\n"
    with open(cfg, "w") as f:
        f.write(text + block)
    print("[ok] added [mcp_servers.claude_chat] to config.toml (backup written).")
PYEOF

# --- 5. register Claude -> Codex MCP server (user scope, absolute path) -------
if [ -n "$CODEX_BIN" ]; then
  if "$CLAUDE_BIN" mcp get codex >/dev/null 2>&1; then
    warn "Claude already has a 'codex' MCP server - leaving it as-is."
  else
    "$CLAUDE_BIN" mcp add codex -s user -- "$CODEX_BIN" mcp-server
    say "registered 'codex' (-> $CODEX_BIN mcp-server) as a user-scope Claude MCP server"
  fi
fi

# --- 6. record paths (single source of truth for the skill) ------------------
mkdir -p "$INSTALL_DIR"
printf '%s\n' "$REPO_DIR" > "$INSTALL_DIR/repo_path"
printf '%s\n' "$REPO_DIR/scripts" > "$INSTALL_DIR/scripts_path"
say "recorded bridge paths -> $INSTALL_DIR/{repo_path,scripts_path}"

# --- 7. install the skill so BOTH agents can discover it ---------------------
# Codex reads ~/.codex/skills/<name>/SKILL.md; Claude reads ~/.claude/skills/.
link_skill() {
  local skills_dir="$1" name="claude-codex-bridge"
  local target="$skills_dir/$name"
  mkdir -p "$skills_dir"
  if [ -L "$target" ]; then
    local cur; cur="$(readlink "$target")"
    if [ "$cur" = "$REPO_DIR/skill" ]; then echo "[ok] skill already linked: $target"; return; fi
    warn "skill symlink exists pointing elsewhere ($cur) — leaving as-is: $target"; return
  elif [ -e "$target" ]; then
    warn "skill path exists and is not our symlink — leaving as-is: $target"; return
  fi
  ln -s "$REPO_DIR/skill" "$target" && say "linked skill -> $target"
}
link_skill "$HOME/.claude/skills"
link_skill "$HOME/.codex/skills"

# --- 8. done -----------------------------------------------------------------
cat <<EOF

Done. claude-codex-bridge is installed (transport + skill).

  - Transport (MCP): Claude<->Codex can call each other.   [restart Codex to load]
  - Skill: discoverable in both Claude and Codex.          [restart agents to load]
  - Colleague tools: $ALLOWED_TOOLS

To collaborate in ANY project, open it and tell either agent ONE thing:

      set up agent collaboration here

The agent runs init-collaboration.sh in that project (creating .collab/ +
AGENTS.md/CLAUDE.md) — or prints you the one command if it has no shell. After
that, every agent opening that project auto-joins. You never type a path.
EOF
