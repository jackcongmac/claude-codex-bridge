#!/usr/bin/env bash
# bridge-identity.sh — per-actor Ed25519 identity for the collaboration board.
#   init --self <actor> [--project DIR]     generate keypair (0600) + register pubkey
#   list [--project DIR]                     show registered principals
#   verify-setup --self <actor> [--project DIR]   sign+verify round-trip self-check
# Private key: ${BRIDGE_KEYS_DIR:-~/.claude-bridge/keys}/<actor>.key
# Public registry: <project>/.collab/keys/allowed_signers  (principal == actor)
set -euo pipefail
NAMESPACE="claude-codex-bridge"
CMD="${1:-}"; shift || true
SELF=""; PROJECT="$PWD"
while [ $# -gt 0 ]; do
  case "$1" in
    --self) SELF="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
command -v ssh-keygen >/dev/null 2>&1 || { echo "[x] ssh-keygen not found — install OpenSSH" >&2; exit 3; }
KEYS_DIR="${BRIDGE_KEYS_DIR:-$HOME/.claude-bridge/keys}"
REG_DIR="$PROJECT/.collab/keys"
ALLOWED="$REG_DIR/allowed_signers"

_register() {
  mkdir -p "$REG_DIR"
  local pub; pub="$(cat "$KEYS_DIR/$SELF.key.pub")"
  local ktype kb64; ktype="$(echo "$pub" | awk '{print $1}')"; kb64="$(echo "$pub" | awk '{print $2}')"
  touch "$ALLOWED"
  # drop any existing line for this principal, then append (rotation-safe)
  grep -v "^$SELF " "$ALLOWED" > "$ALLOWED.tmp" 2>/dev/null || true
  mv "$ALLOWED.tmp" "$ALLOWED"
  echo "$SELF $ktype $kb64" >> "$ALLOWED"
}

case "$CMD" in
  init)
    [ -n "$SELF" ] || { echo "[x] --self <actor> required" >&2; exit 2; }
    mkdir -p "$KEYS_DIR"; chmod 700 "$KEYS_DIR"
    rm -f "$KEYS_DIR/$SELF.key" "$KEYS_DIR/$SELF.key.pub"
    ssh-keygen -t ed25519 -N '' -C "$SELF@$NAMESPACE" -f "$KEYS_DIR/$SELF.key" >/dev/null
    chmod 600 "$KEYS_DIR/$SELF.key"
    _register
    echo "[ok] identity ready for '$SELF' — pubkey registered in $ALLOWED"
    ;;
  list)
    [ -f "$ALLOWED" ] && awk '{print $1}' "$ALLOWED" || echo "(no registered principals)"
    ;;
  verify-setup)
    [ -n "$SELF" ] || { echo "[x] --self <actor> required" >&2; exit 2; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    printf 'selfcheck' > "$tmp/d"
    ssh-keygen -Y sign -f "$KEYS_DIR/$SELF.key" -n "$NAMESPACE" "$tmp/d" >/dev/null
    ssh-keygen -Y verify -f "$ALLOWED" -I "$SELF" -n "$NAMESPACE" -s "$tmp/d.sig" < "$tmp/d" >/dev/null
    echo "[ok] sign+verify round-trip OK for '$SELF'"
    ;;
  *)
    echo "usage: bridge-identity.sh {init|list|verify-setup} --self <actor> [--project DIR]" >&2
    exit 2;;
esac
