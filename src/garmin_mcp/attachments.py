"""Chat attachments for Rabbit: images, PDFs and small text files.

Uploaded bytes are validated by content (magic bytes / UTF-8), not by the
name or the client's content type, then stored Fernet-encrypted per user
(``att-<id>``) and served back only to their owner.

The model sees an attachment only in the turn it was sent with (as an
image/document block); later turns get a ``[kind: name]`` placeholder, while
the UI keeps rendering the real thing from history.
"""

from __future__ import annotations

import base64
import re
import secrets
import time
from typing import Any

from .appstore import AppStore

MAX_IMAGE = 5 * 1024 * 1024
MAX_PDF = 10 * 1024 * 1024
MAX_TEXT = 200 * 1024
MAX_UPLOAD = MAX_PDF
MAX_PER_MESSAGE = 4
_ID = re.compile(r"at[0-9a-f]{16}")

TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".tcx", ".gpx")


class AttachmentError(ValueError):
    """Message is safe to show the user."""


def sniff(data: bytes, name: str) -> tuple[str, str]:
    """(kind, mime) from the content itself."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp"
    if data.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if name.lower().endswith(TEXT_EXTS):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise AttachmentError("That text file isn't UTF-8.") from None
        return "text", "text/plain"
    raise AttachmentError("Unsupported file. Send a photo (PNG/JPEG/WebP/GIF), a PDF, or a text file.")


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f/\\\\]", "", name or "").strip()
    return (name or "attachment")[:120]


class AttachmentStore:
    def __init__(self, store: AppStore, *, clock: Any = time.time) -> None:
        self._s = store
        self._clock = clock

    def save(self, user_id: str, name: str, data: bytes) -> dict[str, Any]:
        name = _clean_name(name)
        kind, mime = sniff(data, name)
        limit = {"image": MAX_IMAGE, "pdf": MAX_PDF, "text": MAX_TEXT}[kind]
        if len(data) > limit:
            raise AttachmentError(f"That {kind} is too large (max {limit // 1024 // 1024 or 0.2} MB).")
        if not data:
            raise AttachmentError("That file is empty.")
        meta = {"id": "at" + secrets.token_hex(8), "name": name, "kind": kind, "mime": mime,
                "size": len(data), "created": int(self._clock())}
        self._s.user_doc(user_id, f"att-{meta['id']}").save(
            {**meta, "data": base64.b64encode(data).decode("ascii")})
        return meta

    def load(self, user_id: str, att_id: str) -> tuple[dict[str, Any], bytes] | None:
        if not isinstance(att_id, str) or not _ID.fullmatch(att_id):
            return None
        d = self._s.user_doc(user_id, f"att-{att_id}").load()
        if not d:
            return None
        data = base64.b64decode(d.pop("data"))
        return d, data

    def for_message(self, user_id: str, ids: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(display metadata, model content blocks) for a message's attachments."""
        if ids in (None, []):
            return [], []
        if not isinstance(ids, list) or len(ids) > MAX_PER_MESSAGE:
            raise AttachmentError(f"Attach up to {MAX_PER_MESSAGE} files per message.")
        metas, blocks = [], []
        for att_id in ids:
            got = self.load(user_id, att_id)
            if got is None:
                raise AttachmentError("An attachment was not found. Try attaching it again.")
            meta, data = got
            metas.append({k: meta[k] for k in ("id", "name", "kind", "mime", "size")})
            b64 = base64.b64encode(data).decode("ascii")
            if meta["kind"] == "image":
                blocks.append({"type": "image", "source": {"type": "base64",
                                                           "media_type": meta["mime"], "data": b64}})
            elif meta["kind"] == "pdf":
                blocks.append({"type": "document", "title": meta["name"],
                               "source": {"type": "base64", "media_type": "application/pdf",
                                          "data": b64}})
            else:
                blocks.append({"type": "document", "title": meta["name"],
                               "source": {"type": "text", "media_type": "text/plain",
                                          "data": data.decode("utf-8")}})
        return metas, blocks
