# EC1/EC3 Cryptographic Identity Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sign every trust-bearing board/ledger write with per-actor Ed25519 keys (SSHSIG) so a spoofed `**Jack:**` can't trigger execution (EC1) and only signature-verified reviews count toward the push gate, with legacy unsigned GO rejected (EC3).

**Architecture:** One crypto seam `scripts/_sig.py` shells out to `ssh-keygen -Y sign/verify` (SSHSIG, namespace `claude-codex-bridge`). Private keys live out-of-tree (`0600`); public keys in a committed `.collab/keys/allowed_signers` registry whose principal == actor name. The review ledger and human chat messages carry a `sig`; `_review.has_approval` and `_chat_execute.decide` verify it, fail-closed.

**Tech Stack:** Python 3 stdlib (`subprocess`, `hashlib`, `tempfile`), OpenSSH `ssh-keygen` (SSHSIG), bash. No pip crypto dependency.

## Global Constraints

- Signing primitive is **SSHSIG via `ssh-keygen -Y sign/verify`**, Ed25519 only. No `cryptography`/`PyNaCl` pip dependency.
- SSHSIG namespace is the exact constant string `claude-codex-bridge` for every sign and verify.
- Private keys: `${BRIDGE_KEYS_DIR:-~/.claude-bridge/keys}/<actor>.key`, mode `0600`, never committed.
- Public registry: `.collab/keys/allowed_signers`, committed; OpenSSH allowed_signers format; principal == actor name from `roles.json`.
- Canonical payload string: `v1|<kind>|<actor>|<context>|<ts>|<nonce>`; `kind` ∈ {`chat`,`review`}.
- Fail-closed: missing/invalid key or signature ⇒ untrusted (never auto-approve/auto-execute).
- `BRIDGE_REQUIRE_SIGNATURES` (default treated as ON when unset or `"1"`): when ON, execution trigger + push gate require valid signatures. `"0"` is the migration/test escape hatch and must log/note loudly.
- Legacy unsigned GO entries no longer count toward approval (approved behavior change).
- No file may call `ssh-keygen` directly except `scripts/_sig.py` and `scripts/bridge-identity.sh`.
- Every task leaves the full suite green: `python3 -m unittest discover -s tests -q`.
- Tests use real `ssh-keygen` with throwaway keys in a temp dir; if `ssh-keygen` is absent the crypto tests `self.skipTest(...)` (do not silently pass).

---

## File Structure

- `scripts/_sig.py` (new) — the crypto seam: payload builders + `sign`/`verify` + key/registry path resolution.
- `scripts/bridge-identity.sh` (new) — `init` / `list` / `verify-setup` for per-actor keys + registry.
- `.collab/keys/allowed_signers` (created at runtime by `init`; the dir is committed via a `.gitkeep`, the file is committed once actors enroll).
- `.gitignore` (modify) — guard the private-key dir if it ever lands in-tree.
- `scripts/_review.py` (modify) — sign on `record`; verify in `has_approval`; reject unsigned/legacy.
- `scripts/bridge-chat-web.py` (modify) — sign the human's message on `/send`; carry `sig` through `format_chat_message` + `parse_chat`.
- `scripts/_chat_execute.py` (modify) — require a valid human signature on the execution-trigger path.
- Test files: `tests/test_sig.py` (new), `tests/test_identity_cli.py` (new), `tests/test_review.py` (modify/extend), `tests/test_chat_web.py` (modify), `tests/test_chat_execute.py` (modify).

---

# PHASE 1 — Crypto seam + identity tooling (no behavior change yet)

### Task 1: `_sig.py` — canonical payloads + sign/verify seam

**Files:**
- Create: `scripts/_sig.py`
- Test: `tests/test_sig.py`

**Interfaces:**
- Consumes: nothing (leaf module; only stdlib + `bridge_common.collab_paths`, `find_project_root`).
- Produces:
  - `NAMESPACE = "claude-codex-bridge"`
  - `private_key_path(actor) -> str`
  - `allowed_signers_path(project) -> str`
  - `canonical(kind, actor, context, ts, nonce) -> str`
  - `chat_payload(msg: dict) -> str`
  - `review_payload(entry: dict) -> str`
  - `sign(actor, payload, project=None) -> str | None`  (armored SSHSIG text, or None if no key / ssh-keygen missing / error)
  - `verify(actor, payload, sig, project=None) -> bool`  (True only if `sig` verifies for `actor` in the registry under NAMESPACE)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sig.py
import os, shutil, subprocess, tempfile, unittest, sys
sys.path.insert(0, "scripts")
import _sig

def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None

def _gen_key(path):
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test",
                    "-f", path], check=True, capture_output=True)

def _pub_line(actor, keypath):
    pub = open(keypath + ".pub").read().split()
    # allowed_signers line: "<principal> <keytype> <base64>"
    return "%s %s %s\n" % (actor, pub[0], pub[1])

class SigTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = os.path.join(self.tmp, "keys"); os.mkdir(self.keys)
        os.environ["BRIDGE_KEYS_DIR"] = self.keys
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".collab", "keys"))
        _gen_key(os.path.join(self.keys, "Jack.key"))
        with open(os.path.join(self.proj, ".collab", "keys", "allowed_signers"), "w") as f:
            f.write(_pub_line("Jack", os.path.join(self.keys, "Jack.key")))

    def tearDown(self):
        os.environ.pop("BRIDGE_KEYS_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sign_then_verify_true(self):
        payload = _sig.canonical("review", "Jack", "abc|GO", "2026-06-30 10:00:00 PDT", "n1")
        sig = _sig.sign("Jack", payload, project=self.proj)
        self.assertTrue(sig)
        self.assertTrue(_sig.verify("Jack", payload, sig, project=self.proj))

    def test_tampered_payload_fails(self):
        p1 = _sig.canonical("review", "Jack", "abc|GO", "t", "n1")
        sig = _sig.sign("Jack", p1, project=self.proj)
        p2 = _sig.canonical("review", "Jack", "abc|FIX-FIRST", "t", "n1")
        self.assertFalse(_sig.verify("Jack", p2, sig, project=self.proj))

    def test_wrong_actor_principal_fails(self):
        payload = _sig.canonical("chat", "Jack", "h", "t", "n1")
        sig = _sig.sign("Jack", payload, project=self.proj)
        self.assertFalse(_sig.verify("Codex", payload, sig, project=self.proj))

    def test_missing_private_key_returns_none(self):
        self.assertIsNone(_sig.sign("NoSuchActor", "p", project=self.proj))

    def test_missing_sig_verifies_false(self):
        self.assertFalse(_sig.verify("Jack", "p", None, project=self.proj))
        self.assertFalse(_sig.verify("Jack", "p", "", project=self.proj))

    def test_chat_payload_binds_text(self):
        m1 = {"speaker": "Jack", "text": "deploy now", "sent_at": "t", "_id": "id1"}
        m2 = {"speaker": "Jack", "text": "deploy NOW", "sent_at": "t", "_id": "id1"}
        self.assertNotEqual(_sig.chat_payload(m1), _sig.chat_payload(m2))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sig -v`
Expected: FAIL/ERROR — `No module named _sig` / attributes missing.

- [ ] **Step 3: Implement `scripts/_sig.py`**

```python
"""_sig.py — the ONE crypto seam. SSHSIG (ssh-keygen -Y) Ed25519 signatures over canonical
payloads, so board/ledger writes are attributable to a registered actor. Fail-closed: any
error, missing key, or missing signature yields an unsigned/untrusted result — never a false
'valid'. Only this module and bridge-identity.sh may invoke ssh-keygen."""
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_common import collab_paths, find_project_root  # noqa: E402

NAMESPACE = "claude-codex-bridge"


def private_key_path(actor):
    base = os.environ.get("BRIDGE_KEYS_DIR") or os.path.expanduser("~/.claude-bridge/keys")
    return os.path.join(base, "%s.key" % actor)


def allowed_signers_path(project):
    root = find_project_root(project) if project else find_project_root(".")
    return os.path.join(collab_paths(root)["dir"], "keys", "allowed_signers")


def canonical(kind, actor, context, ts, nonce):
    return "v1|%s|%s|%s|%s|%s" % (kind, actor, context, ts, nonce)


def chat_payload(msg):
    text = msg.get("text") or ""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return canonical("chat", msg.get("speaker", ""), h,
                     msg.get("sent_at") or "", msg.get("_id") or "")


def review_payload(entry):
    ctx = "%s|%s" % (entry.get("sha") or "", (entry.get("verdict") or "").upper())
    return canonical("review", entry.get("reviewer") or "", ctx,
                     entry.get("ts") or "", entry.get("nonce") or "")


def sign(actor, payload, project=None):
    key = private_key_path(actor)
    if not os.path.exists(key):
        return None
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    try:
        with open(data, "w") as f:
            f.write(payload)
        r = subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", key, "-n", NAMESPACE, data],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        with open(data + ".sig") as sf:
            return sf.read()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        _rmtree(tmp)


def verify(actor, payload, sig, project=None):
    if not sig:
        return False
    allowed = allowed_signers_path(project)
    if not os.path.exists(allowed):
        return False
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    sigf = os.path.join(tmp, "data.sig")
    try:
        with open(data, "w") as f:
            f.write(payload)
        with open(sigf, "w") as f:
            f.write(sig)
        with open(data) as stdin_data:
            r = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", actor,
                 "-n", NAMESPACE, "-s", sigf],
                stdin=stdin_data, capture_output=True, text=True)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        _rmtree(tmp)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_sig -v`
Expected: PASS (6 tests), or SKIP if `ssh-keygen` unavailable.

- [ ] **Step 5: Commit**

```bash
git add scripts/_sig.py tests/test_sig.py
git commit -m "feat(sig): SSHSIG sign/verify seam + canonical payloads (Phase 1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 2: `bridge-identity.sh` — key generation + registry enrollment

**Files:**
- Create: `scripts/bridge-identity.sh`
- Test: `tests/test_identity_cli.py`

**Interfaces:**
- Consumes: `_sig` conventions (key path via `BRIDGE_KEYS_DIR`, registry at `.collab/keys/allowed_signers`, principal == actor).
- Produces CLI: `bridge-identity.sh init --self <actor> [--project DIR]`, `list [--project DIR]`, `verify-setup --self <actor> [--project DIR]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity_cli.py
import os, shutil, subprocess, tempfile, unittest

def _have_sshkeygen():
    return shutil.which("ssh-keygen") is not None

SCRIPT = os.path.join("scripts", "bridge-identity.sh")

class IdentityCliTests(unittest.TestCase):
    def setUp(self):
        if not _have_sshkeygen():
            self.skipTest("ssh-keygen not available")
        self.tmp = tempfile.mkdtemp()
        self.keys = os.path.join(self.tmp, "keys")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".collab"))
        self.env = dict(os.environ, BRIDGE_KEYS_DIR=self.keys)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(["bash", SCRIPT, *args], cwd=".", env=self.env,
                              capture_output=True, text=True)

    def test_init_creates_0600_key_and_registers_pubkey(self):
        r = self._run("init", "--self", "Jack", "--project", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        key = os.path.join(self.keys, "Jack.key")
        self.assertTrue(os.path.exists(key))
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")
        allowed = os.path.join(self.proj, ".collab", "keys", "allowed_signers")
        self.assertIn("Jack ssh-ed25519 ", open(allowed).read())

    def test_verify_setup_roundtrips(self):
        self._run("init", "--self", "Jack", "--project", self.proj)
        r = self._run("verify-setup", "--self", "Jack", "--project", self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reinit_rotates_single_line(self):
        self._run("init", "--self", "Jack", "--project", self.proj)
        self._run("init", "--self", "Jack", "--project", self.proj)
        allowed = os.path.join(self.proj, ".collab", "keys", "allowed_signers")
        lines = [l for l in open(allowed).read().splitlines() if l.startswith("Jack ")]
        self.assertEqual(len(lines), 1)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_identity_cli -v`
Expected: FAIL — script missing (nonzero exit / file not found).

- [ ] **Step 3: Implement `scripts/bridge-identity.sh`**

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_identity_cli -v`
Expected: PASS (3 tests) or SKIP.

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/bridge-identity.sh
git add scripts/bridge-identity.sh tests/test_identity_cli.py
git commit -m "feat(identity): bridge-identity.sh init/list/verify-setup (Phase 1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 3: `.gitignore` guard for private keys

**Files:**
- Modify: `.gitignore`
- Test: `tests/test_sig.py` (add one guard test)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_sig.py
class GitignoreGuardTests(unittest.TestCase):
    def test_private_key_dir_ignored(self):
        gi = open(".gitignore").read()
        self.assertIn(".claude-bridge/keys", gi)
        self.assertIn("*.key", gi)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_sig.GitignoreGuardTests -v`
Expected: FAIL — strings absent.

- [ ] **Step 3: Append to `.gitignore`**

```
# EC1/EC3 identity — never commit private signing keys
.claude-bridge/keys/
*.key
!*.pub
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_sig.GitignoreGuardTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .gitignore tests/test_sig.py
git commit -m "chore(sig): gitignore private signing keys (Phase 1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

# PHASE 2 — EC3: signed reviews + push gate rejects unsigned/legacy

### Task 4: `_review.record` signs the entry

**Files:**
- Modify: `scripts/_review.py` (the `record` function, ~line 66-89)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `_sig.review_payload(entry)`, `_sig.sign(reviewer, payload, project)`.
- Produces: ledger entries now carry `"sig"` (armored SSHSIG string) or `"sig": None` when the reviewer has no key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review.py — add (import _sig, ssh-keygen helpers like test_sig)
def test_record_signs_entry_when_key_present(self):
    # self.proj has .collab/keys/allowed_signers with Claude; BRIDGE_KEYS_DIR has Claude.key
    import _review, _sig, json, os
    _review.record(self.proj, "Claude", "deadbeef", "GO", note="ok")
    led = json.load(open(os.path.join(self.proj, ".collab", "collaboration_reviews.json")))
    entry = led["reviews"][-1]
    self.assertTrue(entry.get("sig"))
    self.assertTrue(_sig.verify("Claude", _sig.review_payload(entry), entry["sig"], project=self.proj))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_review -v`
Expected: FAIL — entry has no `sig`.

- [ ] **Step 3: Modify `record` to sign**

In `scripts/_review.py`, add `import _sig` near the other imports, and after the `entry = {...}` dict is built (before acquiring the lock), sign it:

```python
    entry = {"reviewer": reviewer, "sha": _canonical_sha(project, sha),
             "verdict": (verdict or "").upper(),
             "target": target, "note": note, "bypass": bool(bypass),
             "recorded_by": recorded_by, "ts": now_str(), "nonce": _nonce()}
    entry["sig"] = _sig.sign(reviewer, _sig.review_payload(entry), project=project)
```

(Leave the `_detected_recorder`/`actor_mismatch` check as-is — it stays as a cheap secondary label; the signature is the real authority checked at `has_approval`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_review -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/_review.py tests/test_review.py
git commit -m "feat(review): sign review ledger entries via _sig (Phase 2 EC3)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 5: `has_approval` requires a valid signature; reject unsigned/legacy

**Files:**
- Modify: `scripts/_review.py` (`has_approval`, ~line 91-104)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `_sig.verify(reviewer, _sig.review_payload(entry), entry.get("sig"), project)`, env `BRIDGE_REQUIRE_SIGNATURES`.
- Produces: `has_approval` semantics — when signatures required (default), an entry counts only if verdict approving AND reviewer ≠ exclude_actor AND signature verifies. Unsigned/legacy/forged ⇒ not counted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review.py — add
def test_signed_go_by_other_reviewer_counts(self):
    import _review
    _review.record(self.proj, "Claude", "sha1", "GO")     # Claude has a key
    self.assertTrue(_review.has_approval(self.proj, "sha1", exclude_actor="Codex"))

def test_unsigned_legacy_go_does_not_count(self):
    import _review, json, os
    # write a legacy entry directly: approving, but no sig
    path = os.path.join(self.proj, ".collab", "collaboration_reviews.json")
    json.dump({"reviews": [{"reviewer": "Claude", "sha": "sha2", "verdict": "GO",
                            "bypass": False, "recorded_by": "Claude"}]}, open(path, "w"))
    self.assertFalse(_review.has_approval(self.proj, "sha2", exclude_actor="Codex"))

def test_forged_sig_rejected(self):
    import _review, json, os
    path = os.path.join(self.proj, ".collab", "collaboration_reviews.json")
    json.dump({"reviews": [{"reviewer": "Claude", "sha": "sha3", "verdict": "GO",
                            "bypass": False, "recorded_by": "Claude",
                            "ts": "t", "nonce": "n", "sig": "-----BEGIN SSH SIGNATURE-----\ngarbage\n-----END SSH SIGNATURE-----\n"}]},
              open(path, "w"))
    self.assertFalse(_review.has_approval(self.proj, "sha3", exclude_actor="Codex"))

def test_escape_hatch_allows_legacy_when_disabled(self):
    import _review, json, os
    os.environ["BRIDGE_REQUIRE_SIGNATURES"] = "0"
    try:
        path = os.path.join(self.proj, ".collab", "collaboration_reviews.json")
        json.dump({"reviews": [{"reviewer": "Claude", "sha": "sha4", "verdict": "GO",
                                "bypass": False, "recorded_by": "Claude"}]}, open(path, "w"))
        self.assertTrue(_review.has_approval(self.proj, "sha4", exclude_actor="Codex"))
    finally:
        os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_review -v`
Expected: FAIL — unsigned/forged still counted (current code ignores `sig`).

- [ ] **Step 3: Modify `has_approval`**

Replace the loop body condition in `has_approval`:

```python
def _sigs_required():
    return os.environ.get("BRIDGE_REQUIRE_SIGNATURES", "1") != "0"


def has_approval(project, sha, exclude_actor):
    """True iff a NON-bypass approving (SHIP/GO) verdict exists for `sha` by a reviewer
    other than `exclude_actor`, whose signature verifies. When BRIDGE_REQUIRE_SIGNATURES=0
    (migration/test escape hatch), fall back to the legacy recorded_by check."""
    import _sig
    p = collab_paths(project)
    led = read_json(p["reviews"], default={"reviews": []}) or {"reviews": []}
    require = _sigs_required()
    for e in led.get("reviews", []):
        if (e.get("sha") != sha or e.get("bypass")
                or (e.get("verdict") or "").upper() not in APPROVING
                or not e.get("reviewer") or e.get("reviewer") == exclude_actor):
            continue
        if require:
            if _sig.verify(e.get("reviewer"), _sig.review_payload(e), e.get("sig"), project=project):
                return True
        else:
            recorded_by = e.get("recorded_by")
            if recorded_by is None or recorded_by == e.get("reviewer"):
                return True
    return False
```

(Add `import os` at top if not already imported — it is.)

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `python3 -m unittest tests.test_review -v`
Expected: PASS.
Run: `python3 -m unittest discover -s tests -q`
Expected: OK. (If pre-existing `_review`/`bridge-push` tests assumed unsigned GO counts, update them to either sign via a keyed reviewer or set `BRIDGE_REQUIRE_SIGNATURES=0` in that test — signing is the correct behavior now.)

- [ ] **Step 5: Commit**

```bash
git add scripts/_review.py tests/test_review.py
git commit -m "feat(review): push gate requires verified signature; reject unsigned/legacy GO (Phase 2 EC3)

Co-Authored-By: Codex <codex@openai.com>"
```

---

# PHASE 3 — EC1: signed human chat triggers

### Task 6: carry `sig` through `format_chat_message` + `parse_chat`

**Files:**
- Modify: `scripts/bridge-chat-web.py` (`format_chat_message` ~line 88-101, `parse_chat` ~line 104-149, `_parse_chat_attrs`)
- Test: `tests/test_chat_web.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `format_chat_message(..., sig=None)` writes `sig:<b64>` into the chat-id attrs; `parse_chat` surfaces `msg["sig"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_web.py — add
def test_format_and_parse_roundtrip_sig(self):
    import base64
    fake_sig = base64.b64encode(b"-----BEGIN SSH SIGNATURE-----\nx\n-----END SSH SIGNATURE-----\n").decode()
    line = cw.format_chat_message("Jack", "hello", sig=fake_sig)
    section = "### 2026-06-30 10:00:00 PDT\n\n" + line
    msgs = cw.parse_chat(section)
    self.assertEqual(msgs[-1]["sig"], fake_sig)
```

(Store the signature base64-encoded in the attr so the armored PEM newlines don't break the single-line HTML comment.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_chat_web -v`
Expected: FAIL — `sig` not present.

- [ ] **Step 3: Modify `format_chat_message` and `parse_chat`**

In `format_chat_message`, add a `sig=None` parameter and include it in the attrs when present. The current signature is `format_chat_message(speaker, text, msg_id=None, sent_at=None, send_trigger=None, img=None)`; extend to `(..., img=None, sig=None)`. Where it builds the attr string (alongside `sent_at`/`img`), append ` sig:<sig>` when `sig` is truthy (the value is already base64, single-line).

In `_parse_chat_attrs` + `parse_chat`, recognize the `sig` attribute and set `msg["sig"] = attrs["sig"]` when present (mirror exactly how `img` is handled at ~line 130-136).

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_chat_web -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bridge-chat-web.py tests/test_chat_web.py
git commit -m "feat(chat): carry base64 signature through format/parse chat message (Phase 3 EC1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 7: web server signs the human's message on `/send`

**Files:**
- Modify: `scripts/bridge-chat-web.py` (the `/send` POST handler ~line 449-471, and wherever it calls `format_chat_message`/posts)
- Test: `tests/test_chat_web.py`

**Interfaces:**
- Consumes: `_sig.sign(self_human, _sig.chat_payload(msg_like), project)`; `_sig.chat_payload`.
- Produces: messages posted by the human carry a valid `sig` (base64-encoded) computed over `{speaker, text, sent_at, _id}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_web.py — add (server bound to a temp project + Jack key present)
def test_send_signs_human_message(self):
    import base64, _sig
    # self._server started with --self Jack and a Jack key registered in the temp project
    self._post("/send", {"text": "please refactor", "sent_at": "2026-06-30T10:00:00",
                         "send_trigger": "click"})
    msgs = cw.parse_chat(cw.read_section(self._board(), "Chat"))
    m = msgs[-1]
    self.assertEqual(m["speaker"], "Jack")
    self.assertTrue(m.get("sig"))
    raw = base64.b64decode(m["sig"]).decode()
    self.assertTrue(_sig.verify("Jack", _sig.chat_payload(m), raw, project=self._proj))
```

(Note for the test author: the existing chat-web test harness must generate a `Jack.key` + `allowed_signers` in the temp project and set `BRIDGE_KEYS_DIR`, mirroring `tests/test_sig.py setUp`. If the harness starts a real server, sign within the handler; assert the stored message verifies.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_chat_web -v`
Expected: FAIL — message has no `sig`.

- [ ] **Step 3: Modify the `/send` handler**

In the `/send` handler, after computing the human's `text`/`sent_at` and generating the `msg_id` (via `format_chat_message`'s default or an explicit id), build the payload and sign with the server's configured human identity (`self_name`), base64-encode the armored signature, and pass `sig=` into `format_chat_message`. Concretely: generate the `msg_id` first (call `secrets.token_hex(8)` and pass it explicitly so the signed `_id` matches the stored `_id`), build `msg_like = {"speaker": self_name, "text": text, "sent_at": sent_at, "_id": msg_id}`, `raw = _sig.sign(self_name, _sig.chat_payload(msg_like), project=project)`, `sig = base64.b64encode(raw.encode()).decode() if raw else None`, then `format_chat_message(self_name, text, msg_id=msg_id, sent_at=sent_at, send_trigger=trigger, img=img, sig=sig)`. Import `base64` and `_sig` at the top of the module. If `raw` is None (no key), post unsigned (fail-closed downstream — it just won't trigger execution).

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_chat_web -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bridge-chat-web.py tests/test_chat_web.py
git commit -m "feat(chat): server signs human message on send with the human key (Phase 3 EC1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

### Task 8: `_chat_execute.decide` requires a valid human signature to trigger

**Files:**
- Modify: `scripts/_chat_execute.py` (`decide`, ~line 41-45)
- Test: `tests/test_chat_execute.py`

**Interfaces:**
- Consumes: `_sig.verify(human, _sig.chat_payload(latest), _b64decode(latest.get("sig")), project)`; env `BRIDGE_REQUIRE_SIGNATURES`; `_chat_roles.human_name(project)`.
- Produces: `decide` returns `{"action":"ignore","reason":"unsigned-human"}` when signatures are required and the latest human message lacks a valid signature; otherwise proceeds as today.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_execute.py — add
def test_unsigned_human_message_does_not_trigger(self):
    import _chat_execute as ce
    # BRIDGE_REQUIRE_SIGNATURES default on; msg from human with no sig
    msgs = [{"speaker": "Jack", "text": "改README", "sent_at": "t", "_id": "id1"}]
    d = ce.decide(self.proj, msgs, judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
    self.assertEqual(d["action"], "ignore")
    self.assertEqual(d.get("reason"), "unsigned-human")

def test_validly_signed_human_message_triggers(self):
    import _chat_execute as ce, _sig, base64
    msg = {"speaker": "Jack", "text": "改README", "sent_at": "t", "_id": "id1"}
    raw = _sig.sign("Jack", _sig.chat_payload(msg), project=self.proj)   # Jack keyed in setUp
    msg["sig"] = base64.b64encode(raw.encode()).decode()
    d = ce.decide(self.proj, [msg], judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
    self.assertEqual(d["action"], "execute")

def test_signatures_disabled_allows_unsigned(self):
    import _chat_execute as ce, os
    os.environ["BRIDGE_REQUIRE_SIGNATURES"] = "0"
    try:
        msgs = [{"speaker": "Jack", "text": "改README", "sent_at": "t", "_id": "id1"}]
        d = ce.decide(self.proj, msgs, judge=lambda t, c, image_path=None: {"kind": "actionable", "task": "改README"})
        self.assertEqual(d["action"], "execute")
    finally:
        os.environ.pop("BRIDGE_REQUIRE_SIGNATURES", None)
```

(The `test_chat_execute.py` setUp must, for the signed-trigger test, create a Jack key + registry in `self.proj` and set `BRIDGE_KEYS_DIR`, mirroring `tests/test_sig.py`. The existing `decide` tests that pass bare `**Jack:**` dicts must set `BRIDGE_REQUIRE_SIGNATURES=0` OR be updated to sign — otherwise they now correctly return `ignore/unsigned-human`. Update them so the suite stays green.)

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_chat_execute -v`
Expected: FAIL — unsigned message still triggers.

- [ ] **Step 3: Modify `decide`**

```python
import base64
import _sig

def _human_sig_ok(project, msg):
    if os.environ.get("BRIDGE_REQUIRE_SIGNATURES", "1") == "0":
        return True
    sig_b64 = msg.get("sig")
    if not sig_b64:
        return False
    try:
        raw = base64.b64decode(sig_b64).decode()
    except (ValueError, UnicodeDecodeError):
        return False
    return _sig.verify(msg.get("speaker", ""), _sig.chat_payload(msg), raw, project=project)


def decide(project, msgs, judge):
    if not msgs:
        return {"action": "none"}
    latest = msgs[-1]
    if not _chat_roles.is_human(latest.get("speaker", ""), project):
        return {"action": "ignore", "reason": "not-human"}
    if not _human_sig_ok(project, latest):
        return {"action": "ignore", "reason": "unsigned-human"}
    image_path = _image_path_for(project, latest)
    verdict = judge(latest.get("text", ""), msgs[:-1], image_path) or {}
    # ... rest unchanged ...
```

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `python3 -m unittest tests.test_chat_execute -v`
Expected: PASS.
Run: `python3 -m unittest discover -s tests -q`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/_chat_execute.py tests/test_chat_execute.py
git commit -m "feat(chat-exec): require verified human signature to trigger execution (Phase 3 EC1)

Co-Authored-By: Codex <codex@openai.com>"
```

---

## Post-plan enrollment (one-time, after code lands + review)

Not a code task — the operator runs once so the feature is usable and the suite's live paths work:

```bash
scripts/bridge-identity.sh init --self Jack   --project .
scripts/bridge-identity.sh init --self Claude --project .
scripts/bridge-identity.sh init --self Codex  --project .
git add .collab/keys/allowed_signers && git commit -m "chore(identity): enroll actor public keys"
```

(Private keys stay on each actor's machine; only `allowed_signers` is committed.)
