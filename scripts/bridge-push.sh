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

WHO=""; NO_REVIEW=0
for a in "$@"; do
  case "$a" in
    --no-review) NO_REVIEW=1;;
    --*) echo "unknown arg: $a" >&2; exit 2;;
    *) [ -z "$WHO" ] && WHO="$a";;
  esac
done
WHO="${WHO:-${BRIDGE_AGENT:-unknown}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${BRIDGE_PUSH_REPO:-$(git rev-parse --show-toplevel)}"
LOCK="$REPO/.bridge_push.lock"
BRANCH="${BRIDGE_PUSH_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
TTL="${BRIDGE_PUSH_TTL:-180}"          # seconds before a held lock is considered stale
WAIT_MAX="${BRIDGE_PUSH_WAIT:-120}"    # seconds to wait for a peer's push before giving up

now() { date +%s; }
jget() { _L="$LOCK" _K="$1" python3 -c "import json,os;print(json.load(open(os.environ['_L']))[os.environ['_K']])" 2>/dev/null || echo "$2"; }

# release: owner-checked — only remove the lock if WE still hold it (its pid is ours),
# so a late release can't delete a DIFFERENT pusher's freshly-acquired lock.
release() {
  if [ "$(jget pid -1)" = "$$" ]; then rm -f "$LOCK"; fi
}

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
      # ATOMIC stale break: CLAIM the break by renaming the stale lock to a unique
      # name. `mv` of a given source succeeds for exactly one racer; the loser finds
      # the source gone and just retries (then sees the winner's fresh lock). This
      # stops two pushers both breaking the same stale lock and both proceeding.
      echo "[!] breaking stale push lock (held by $who for >${TTL}s)"
      if mv "$LOCK" "$LOCK.breaking.$$" 2>/dev/null; then
        rm -f "$LOCK.breaking.$$"
      fi
      continue
    fi
    if [ "$waited" -ge "$WAIT_MAX" ]; then
      echo "[x] push lock held by $who (pid $pid) — gave up after ${waited}s." >&2
      echo "    If you're sure it's dead: rm $LOCK" >&2
      return 1
    fi
    echo "[..] $who is pushing; waiting (${waited}s)…"; sleep 3; waited=$(( waited + 3 ))
  done
}

# Test seam: source the script to load the lock helpers without running the push.
if [ "${BRIDGE_PUSH_SOURCE_ONLY:-}" = "1" ]; then return 0 2>/dev/null || exit 0; fi

# --- review gate: refuse to push a commit no PEER has approved ----------------
# Only when this repo runs a collaboration board; a plain repo is never gated. The
# gate checks the CURRENT HEAD (the commit the author got reviewed) BEFORE the rebase
# below rewrites SHAs. Default hard-reject; --no-review bypasses with an audit entry.
# A repo is collaboration-enabled if it has a .collab/ dir or a legacy board marker;
# fail CLOSED (gate ON) whenever any board marker is present. The gate is always on
# for such repos — the ONLY escape is the audited --no-review (no silent env switch).
# Match bridge_common.collab_paths' flat markers so a legacy board repo isn't skipped.
board_present() {
  [ -d "$REPO/.collab" ] \
    || [ -f "$REPO/collaboration.md" ] || [ -f "$REPO/collaboration_signal.json" ] \
    || [ -f "$REPO/collaboration_state.json" ] || [ -f "$REPO/collaboration_queue.json" ]
}
HEAD_SHA=""
if board_present; then
  HEAD_SHA="$(git rev-parse HEAD)"
  if [ "$NO_REVIEW" = "1" ]; then
    echo "[!] --no-review: bypassing the review gate for $HEAD_SHA (recording an audit entry)."
    # The audit entry IS the accountability for a bypass — if it can't be written,
    # refuse rather than push unaudited.
    if ! python3 "$HERE/_review.py" record --self "$WHO" --sha "$HEAD_SHA" \
         --verdict BYPASS --bypass --note "push --no-review" --project "$REPO"; then
      echo "[x] --no-review: could not WRITE the audit entry (lock busy?) — refusing to push unaudited." >&2
      exit 5
    fi
  elif ! python3 "$HERE/_review.py" check --sha "$HEAD_SHA" --exclude "$WHO" --project "$REPO" >/dev/null 2>&1; then
    echo "[x] review gate: HEAD $HEAD_SHA has no SHIP/GO recorded by a peer (not '$WHO')." >&2
    echo "    Record a peer review, then re-push:" >&2
    echo "      $HERE/bridge-review.sh --self <peer> --sha $HEAD_SHA --verdict SHIP" >&2
    echo "    Or bypass (audited):  $HERE/bridge-push.sh $WHO --no-review" >&2
    exit 4
  fi
fi

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
# The rebase above may have rewritten HEAD. Tie the actually-pushed SHA back to the
# gate-approved one in the ledger so an auditor can trace what shipped on what review.
if [ -n "$HEAD_SHA" ]; then
  PUSH_SHA="$(git rev-parse HEAD)"
  if [ "$PUSH_SHA" != "$HEAD_SHA" ]; then
    if ! python3 "$HERE/_review.py" record --self "$WHO" --sha "$PUSH_SHA" --verdict PUSHED \
         --bypass --note "rebased from gate-approved $HEAD_SHA" --project "$REPO"; then
      echo "[x] could not record the pushed-SHA audit trace (lock busy?) — refusing to push untraceable." >&2
      exit 5
    fi
  fi
fi
git push origin "$BRANCH"
echo "[ok] pushed '$BRANCH'. Lock released."
