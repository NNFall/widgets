from __future__ import annotations

import hashlib
import secrets


def token_digest(raw: str) -> str:
    """Return the stable digest persisted for an opaque browser token."""

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token() -> tuple[str, str]:
    """Issue a browser-safe token and its persistence-safe digest."""

    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)
