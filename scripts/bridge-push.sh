#!/usr/bin/env bash
#
# bridge-push.sh — coordinate `git push` between two agents sharing one repo.
#
# Two agents (Claude + Codex) pushing to the same branch at the same time race
# and collide. This serializes pushes with a lock file whose PRESENCE is the
# "someone is pushing" signal and whose ABSENCE means clear. Flow:
#   acquire lock -> git pull --rebase -> git push -> release lock.
#
# Usage:  scripts/bridge-push.sh <who>      # e.g. claude | codex
#         BRIDGE_AGENT=claude scripts/bridge-push.sh
# Commit your work FIRST; this only syncs + pushes under the lock.
#
# NOTE: the lock prevents SIMULTANEOUS pushes. It does NOT prevent conflicts from
# both agents editing the SAME file — that needs lane discipline (see
# docs/agent-collaboration-protocol.md). If `pull --rebase` hits a conflict this
# script stops and leaves the rebase for you to resolve.
set -euo pipefail

WHO="${1:-${BRIDGE_AGENT:-unknown}}"
REPO="$(git rev-parse --show-toplevel)"
LOCK="$REPO/.bridge_push.lock"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
TTL="${BRIDGE_PUSH_TTL:-180}"          # seconds before a held lock is considered stale
WAIT_MAX="${BRIDGE_PUSH_WAIT:-120}"    # seconds to wait for a peer's push before giving up

now() { date +%s; }
jget() { _L="$LOCK" _K="$1" python3 -c "import json,os;print(json.load(open(os.environ['_L']))[os.environ['_K']])" 2>/dev/null || echo "$2"; }

release() { rm -f "$LOCK"; }

acquire() {
  local waited=0
  while true; do
    # atomic create-if-absent
    if ( set -o noclobber
         printf '{"who":"%s","pid":%s,"host":"%s","since":%s}\n' \
                "$WHO" "$$" "$(hostname)" "$(now)" > "$LOCK" ) 2>/dev/null; then
      return 0
    fi
    local since who pid; since="$(jget since 0)"; who="$(jget who '?')"; pid="$(jget pid -1)"
    if [ "$(( $(now) - since ))" -gt "$TTL" ]; then
      echo "[!] breaking stale push lock (held by $who for >${TTL}s)"; rm -f "$LOCK"; continue
    fi
    if [ "$waited" -ge "$WAIT_MAX" ]; then
      echo "[x] push lock held by $who (pid $pid) — gave up after ${waited}s." >&2
      echo "    If you're sure it's dead: rm $LOCK" >&2
      return 1
    fi
    echo "[..] $who is pushing; waiting (${waited}s)…"; sleep 3; waited=$(( waited + 3 ))
  done
}

acquire || exit 1
trap release EXIT
echo "[==>] push lock acquired by '$WHO' on branch '$BRANCH'"

git fetch -q origin "$BRANCH"
# Rebase non-interactively, dropping now-empty/duplicate commits. Only a REAL
# conflict (unmerged files) is a failure; a benign pause is auto-continued.
GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true git -c rebase.autoStash=true \
  rebase --empty=drop "origin/$BRANCH" >/dev/null 2>&1 || true
tries=0
while [ -d "$(git rev-parse --git-path rebase-merge 2>/dev/null)" ] || \
      [ -d "$(git rev-parse --git-path rebase-apply 2>/dev/null)" ]; do
  if git diff --name-only --diff-filter=U | grep -q .; then
    echo "[x] real conflict — both agents edited the same file." >&2
    echo "    Resolve it, then re-run bridge-push.sh. (lane discipline: docs/agent-collaboration-protocol.md)" >&2
    exit 2   # trap releases the lock; rebase left for you to resolve
  fi
  tries=$(( tries + 1 )); [ "$tries" -gt 50 ] && { echo "[x] rebase stuck; resolve manually." >&2; exit 3; }
  GIT_EDITOR=true git rebase --continue >/dev/null 2>&1 || git rebase --skip >/dev/null 2>&1 || break
done
git push origin "$BRANCH"
echo "[ok] pushed '$BRANCH'. Lock released."
