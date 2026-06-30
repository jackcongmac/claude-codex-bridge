#!/usr/bin/env python3
"""Image upload storage helpers for bridge chat.

This module is the trust boundary for uploaded bytes. Callers may pass a
content-type hint, but persistence decisions are made only from magic bytes.
"""
import os
import re
import secrets

from bridge_common import collab_paths, find_project_root


ALLOWED = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{8,}\.(png|jpg|gif|webp)$")
_EXT_TO_CONTENT_TYPE = {ext: content_type for content_type, ext in ALLOWED.items()}


def sniff_image_type(raw_bytes):
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw_bytes[0:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def _uploads_dir(project):
    cdir = collab_paths(find_project_root(project))["dir"]
    return os.path.join(cdir, "chat_uploads")


def save_image(project, raw_bytes, content_type=None):
    """Validate and persist an uploaded image, returning its server-side ref."""
    del content_type  # the caller's hint is intentionally ignored.

    if not raw_bytes:
        raise ValueError("empty upload")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("upload too large")

    sniffed = sniff_image_type(raw_bytes)
    if sniffed not in ALLOWED:
        raise ValueError("unsupported image type")

    uploads_dir = _uploads_dir(project)
    os.makedirs(uploads_dir, exist_ok=True)

    ext = ALLOWED[sniffed]
    while True:
        ref = "%s.%s" % (secrets.token_hex(16), ext)
        path = os.path.join(uploads_dir, ref)
        try:
            with open(path, "xb") as f:
                f.write(raw_bytes)
            return ref
        except FileExistsError:
            continue


def load_image(project, image_id):
    """Load a saved image ref, returning (bytes, content_type) or None."""
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.match(image_id):
        return None

    ext = image_id.rsplit(".", 1)[1]
    content_type = _EXT_TO_CONTENT_TYPE.get(ext)
    if content_type is None:
        return None

    path = os.path.join(_uploads_dir(project), image_id)
    try:
        with open(path, "rb") as f:
            return f.read(), content_type
    except (FileNotFoundError, IsADirectoryError):
        return None
