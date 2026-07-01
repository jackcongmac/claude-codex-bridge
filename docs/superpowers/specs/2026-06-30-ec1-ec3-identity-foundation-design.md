# EC1/EC3 Cryptographic Identity Foundation — Design Spec

**Date:** 2026-06-30
**Author:** Claude (lead), design approved by Jack
**Status:** Approved design → ready for implementation plan

## 1. Goal

Make every trust-bearing write on the collaboration board cryptographically attributable to
a verified actor, and enforce that attribution at the two trust boundaries that matter:

- **EC1 — human trigger authenticity:** a chat message only counts as a human execution
  trigger if it carries a valid signature from the configured human's registered key. A
  spoofed `**Jack:**` line written directly to the board by any other process cannot trigger
  execution.
- **EC3 — reviewer/pusher identity:** a review "GO" only counts toward the push gate if its
  signature verifies against the reviewer's registered public key (and reviewer ≠ pusher).
  Legacy unsigned GO entries no longer count. The author can no longer self-certify a GO or
  replay an unsigned/legacy one.

This is the cryptographic **foundation** for a multi-user / shared environment: verifiers need
only public keys, so it extends to multiple humans and machines later.

## 2. Non-goals (explicitly deferred)

These are the "heaviest threat model" pieces we are **not** building in this spec (they layer
on top of this foundation later):

- Interactive **login / per-user web authentication** (the web server trusts a locally
  configured human key for now, not a login session).
- **TLS / network transport security** (the board is still local files; the web server still
  binds `127.0.0.1`).
- **Cross-machine key distribution** (public keys are shared via the git-committed registry;
  no key server / remote enrollment).

Deferring these is safe: they are additive. The signing/verification seam built here is what
they would plug into.

## 3. Threat model (what this defends)

Adversary: any process (a buggy/naive agent, a stray script, a second collaborator on a shared
board) that can **write to the board file** and thereby fabricate a `**Jack:**` trigger or a
`reviewer:Claude GO`. This exactly reproduces the already-observed governance breach (an agent
forged `reviewer:Claude` ledger entries to self-pass the push gate).

Explicit boundary: on a single machine where an adversary already has code-execution **as the
same OS user**, they own the private keys and git credentials and can bypass any application-
layer check — that is not a boundary application crypto can hold, and we do not claim it. The
value here is (a) killing board-write spoofing/forgery, and (b) laying the multi-user-ready
foundation where identities are keys, not self-asserted names/env vars.

## 4. Architecture overview

```
actor (Jack/Claude/Codex)
  ├─ private key  (out-of-tree, 0600)         ── signs its own writes
  └─ public key   (.collab/keys/, committed)  ── everyone verifies with it

writes → _sig.sign(actor, payload) → signature stored in the entry
reads/trust boundaries → _sig.verify(actor, payload, sig) → bool (fail-closed)

Trust boundaries:
  EC1  _chat_execute.decide  : human trigger requires valid human signature
  EC3  _review.has_approval  : GO requires valid reviewer signature; unsigned/legacy rejected
       bridge-push.sh gate   : enforces the above before any push
```

Single crypto seam: **`scripts/_sig.py`**. Nothing else calls `ssh-keygen` directly.

## 5. Signing primitive — SSHSIG via `ssh-keygen`

Chosen (approved): **`ssh-keygen -Y sign` / `-Y verify`** (the SSHSIG format), Ed25519 keys.
Rationale: zero Python dependency, present on every macOS/Linux dev machine, matches the
project's existing shell-out style (git/codex/claude). No `cryptography`/`PyNaCl` pip dep.

**Namespace (domain separation):** `claude-codex-bridge` (constant), passed as `-n`. Prevents a
signature made for this tool being replayed as an SSH auth or another app's SSHSIG.

**Key generation** (`bridge-identity.sh init --self <actor>`):
```
ssh-keygen -t ed25519 -N '' -C "<actor>@claude-codex-bridge" -f <PRIV_KEY_PATH>
# then register the .pub into the allowed_signers registry (below)
```

**Sign** (`_sig.sign(actor, payload)`), payload delivered via a temp file / stdin:
```
printf '%s' "<payload>" | ssh-keygen -Y sign -f <PRIV_KEY_PATH> -n claude-codex-bridge -
# → armored SSHSIG blob on stdout; store it (base64/armored text) in the entry
```

**Verify** (`_sig.verify(actor, payload, sig)`):
```
printf '%s' "<payload>" | ssh-keygen -Y verify \
    -f <ALLOWED_SIGNERS> -I "<actor-principal>" -n claude-codex-bridge -s <SIG_FILE>
# exit 0 = valid; nonzero = invalid → verify() returns False
```

`ssh-keygen` absent → `init` hard-errors telling the user to install OpenSSH; `sign`/`verify`
fail-closed (sign → unsigned/untrusted; verify → False).

## 6. Keys, registry, and identity binding

- **Private key:** `${BRIDGE_KEYS_DIR:-~/.claude-bridge/keys}/<actor>.key`, mode `0600`,
  **never committed** (also add a `.gitignore` guard). One per actor per machine.
- **Public registry (allowed_signers):** `.collab/keys/allowed_signers`, **committed to git**.
  OpenSSH allowed_signers format, one line per actor:
  ```
  <actor-principal> ssh-ed25519 AAAA... <comment>
  ```
  where `<actor-principal>` = the actor name from `roles.json` (e.g. `Jack`, `Claude`, `Codex`).
- **Principal ↔ actor:** the principal string IS the actor name. `verify(actor, …)` passes
  `-I <actor>`; ssh-keygen only accepts the signature if it was made by the key registered for
  that principal → this is the identity binding that replaces env-var `_detected_recorder`.
- **Registering a pubkey:** `bridge-identity.sh init` appends/updates the actor's line in
  `allowed_signers`. Re-init for an existing actor rotates the key (replaces the line).

## 7. Canonical payload

Deterministic, versioned, domain-tagged string signed/verified for each event kind:
```
v1|<kind>|<actor>|<context>|<ts>|<nonce>
```
- `kind` ∈ {`chat`, `review`}.
- `chat` context = `sha256(message_text)` hex (binds the signature to the exact message text;
  the human's key signs what they actually said). **Canonical-text rule:** the server must sign
  over the SAME text that `parse_chat` will later return — i.e. sign the post-`sanitize_chat_text`
  stored form, not the raw input — so the sign-side and verify-side hashes always agree.
  `_sig.chat_payload(msg)` computes the hash from `msg["text"]` (the parsed/stored text).
- `review` context = `<full_commit_sha>|<VERDICT>` (binds GO/FIX-FIRST to a specific commit).
- `ts` = the entry timestamp (`now_str()`); `nonce` = existing per-entry nonce.
- All fields `|`-joined; any field containing `|` is disallowed/escaped (actor names, sha,
  verdict, ts, nonce never contain `|`; text is hashed so it can't).

Storing the signature:
- **Chat:** in the chat-id HTML-comment attrs (same mechanism as `sent_at`/`img`), a new
  `sig=<base64>` attribute; parse_chat surfaces `msg["sig"]`.
- **Review ledger:** a new `sig` field on the JSON entry alongside `recorded_by`/`nonce`.

## 8. Trust boundaries — enforcement

### EC1 — execution trigger (in `_chat_execute` / `_chat_roles`)
- `decide()` treats a message as a human trigger only if BOTH: `is_human(speaker)` AND
  `_sig.verify("<human>", chat_payload(msg), msg["sig"])` is True.
- If unsigned/invalid: the message is still shown in chat, but `decide()` returns
  `{"action":"ignore","reason":"unsigned-human"}` → no execution. (Fail-closed.)
- Gated by `BRIDGE_REQUIRE_SIGNATURES` (see §10). When signatures are required and the human has
  no registered key, no message can trigger — fail-closed, with a one-time board note.

### EC3 — push gate (in `_review.py` + `bridge-push.sh`)
- `record()` signs the review payload with the reviewer's key and stores `sig`. If signing
  fails (no key), the entry is recorded **unsigned** and is treated as non-approving.
- `has_approval(sha, exclude_actor)` counts an entry as approving only if: verdict ∈ APPROVING,
  reviewer ≠ exclude_actor, AND `_sig.verify(reviewer, review_payload(entry), entry["sig"])`
  is True. **Legacy entries without a valid `sig` are NOT counted** (this is the approved
  behavior change — legacy unsigned GO ceases to count).
- `bridge-push.sh` continues to call the gate; no change to its contract beyond the stricter
  `has_approval`.
- `_detected_recorder` env heuristic is demoted to a best-effort label only; the signature is
  the authority.

## 9. Components (files, responsibilities, interfaces)

| File | Responsibility |
|---|---|
| `scripts/_sig.py` (new) | The crypto seam. `sign(actor, payload)->sig_or_None`; `verify(actor, payload, sig)->bool`; `chat_payload(msg)`, `review_payload(entry)`; registry + key path resolution; namespace constant. Fail-closed. |
| `scripts/bridge-identity.sh` (new) | `init --self <actor>` (gen keypair + register pubkey), `list`, `verify-setup --self <actor>` (self-check sign→verify round-trip). |
| `.collab/keys/allowed_signers` (new, committed) | Public registry (principal→ed25519 pubkey). |
| `scripts/_review.py` | Sign on `record`; verify in `has_approval`; reject unsigned/legacy GO. |
| `scripts/bridge-chat-web.py` | On human send, sign `chat` payload with the human's key; store `sig=` in msg attrs. `parse_chat` surfaces `sig`. |
| `scripts/_chat_execute.py` / `_chat_roles.py` | Require valid human signature for the execution-trigger path. |
| `scripts/bridge-push.sh` | Unchanged contract; benefits from stricter `has_approval`. |
| `.gitignore` | Ensure private-key dir never committed. |

## 10. Configuration

- `BRIDGE_REQUIRE_SIGNATURES` (default `1` once this ships): when `1`, execution + push gate
  require valid signatures (fail-closed). A `0` escape hatch exists only for the migration
  window / tests, and logs loudly.
- `BRIDGE_KEYS_DIR` — private-key directory (default `~/.claude-bridge/keys`).
- `roles.json` — unchanged (`human`, `lead`); actor names double as signing principals.

## 11. Error handling / fail-closed matrix

| Condition | Result |
|---|---|
| Private key missing when signing | write recorded **unsigned** → untrusted for execution/push; board note |
| Public key missing in registry when verifying | `verify()` → False → untrusted |
| Signature tampered / wrong actor / wrong namespace | `verify()` → False → untrusted |
| `ssh-keygen` not installed | `init` hard-errors; `sign`→None; `verify`→False |
| `BRIDGE_REQUIRE_SIGNATURES=1` and no human key | no message can trigger execution (fail-closed) |

Principle: ambiguity always resolves to **untrusted**, never to auto-approve/auto-execute.

## 12. Migration / rollout

1. Land the code with `BRIDGE_REQUIRE_SIGNATURES` defaulting on for the gate/execution paths.
2. Each actor runs `bridge-identity.sh init --self <actor>` once (generates key, registers
   pubkey); commit the updated `allowed_signers`.
3. Existing **unsigned** review entries stop counting → any in-flight approval must be
   re-recorded by a keyed reviewer. Ledger is tiny and the executor is OFF, so cost is
   negligible.
4. Board chat history (unsigned) remains as chat; only new signed human messages can trigger —
   and execution stays OFF until this is verified end-to-end regardless.

## 13. Testing strategy

- **`_sig`**: sign→verify round-trip True; tampered payload → False; wrong actor principal →
  False; wrong namespace → False; missing private key → `sign` None; missing pubkey → `verify`
  False. (Use throwaway Ed25519 keys generated in a temp dir; real `ssh-keygen`.)
- **`_review`**: signed GO by reviewer≠pusher counts; unsigned/legacy GO does NOT count; forged
  sig (valid format, wrong key) rejected; FIX-FIRST never counts.
- **`_chat_execute`**: unsigned `**Jack:**` → no trigger (`ignore/unsigned-human`); validly
  signed Jack message → triggers; signature over different text (tamper) → no trigger.
- **`bridge-identity.sh`**: `init` creates 0600 key + registers pubkey; `verify-setup` round-
  trips; re-init rotates.
- **Full suite** stays green; new tests skip gracefully if `ssh-keygen` is unavailable (CI note),
  but must run locally on macOS/Linux.

## 14. Implementation phasing (for the plan)

1. **Phase 1 — crypto seam + identity tooling:** `_sig.py`, `bridge-identity.sh`,
   `allowed_signers` registry, `.gitignore` guard, `_sig` tests. (No behavior change yet.)
2. **Phase 2 — EC3:** sign reviews in `_review.record`; verify in `has_approval`; reject
   legacy unsigned GO; tests. (Independently shippable + testable.)
3. **Phase 3 — EC1:** sign human chat messages in the web server; require valid human signature
   in the execution-trigger path; tests. (Builds on Phase 1.)

Each phase is independently reviewable and leaves the tree green.
