# v0.9.0 — Group chat that drives real work

The bridge gains a live group-chat room where you, Claude, and Codex share one thread — and the
conversation now drives the actual backlog and (optionally) real execution, with cryptographic trust
underneath. No breaking changes from v0.8.0.

## Group chat
- A shared room (`bridge-chat.sh`, web UI): human + both agents in one live thread.
- Warm, low-latency responders (session-resume) so replies feel live and keep conversational context.
- @mentions with keyboard nav; multi-project isolated windows (each project its own stable port + window);
  image drag/paste upload; XSS-safe markdown rendering; graceful send status (sending / delivered / retry).
- Honest presence: the UI tells you whether an agent is a **writable pane** (can act) or a **read-only
  responder** (can only reply), so you always know who you're really talking to.

## Chat → execution ("talk, and it lands")
- A **signed** human requirement is classified and auto-recorded into `.collab/ISSUES.md` — a single
  source of truth — as an always-on capture, so the chat drives the backlog without you relaying it by hand.
- An opt-in executor (`BRIDGE_CHAT_EXECUTE=1`) turns a greenlit directive into implement → cross-review →
  push, fail-closed. High-risk actions (release/deploy/delete) require an explicit human greenlight.

## Cryptographic identity & mechanized trust
- SSHSIG / Ed25519 signing (`bridge-identity.sh`, `_sig.py`): human chat triggers are signed, reviews are
  signed, and the push gate rejects unsigned/legacy approvals and content-modifying rebases (patch-id check).
- An author can no longer self-certify a review or replay an approval — identity is anchored to keys.

## Symmetric roles
- Either agent can lead; a role→capability registry (`_agent_cli.py`) spawns the right CLI per role.
- lead=Codex actuation is unblocked (review-ledger identity is anchored to the signing key, not the
  ambient process environment).

## Under the hood
- Handled-state machine + claim locks (no double-execution), sandboxed implementer (workspace-write, no
  network), push serialization lock, and broad protocol-layer tests (~540).

Install or update: `npm i -g @jackcongus/claude-codex-bridge` (or `git pull` your clone).
