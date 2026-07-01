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
