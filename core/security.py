from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from typing import Final

HASH_NAME: Final[str] = "sha256"
ITERATIONS: Final[int] = 120_000
SALT_LEN: Final[int] = 16


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(SALT_LEN)
    derived = hashlib.pbkdf2_hmac(HASH_NAME, password.encode("utf-8"), salt, ITERATIONS)
    return base64.b64encode(salt + derived).decode("ascii")


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        decoded = base64.b64decode(stored_hash.encode("ascii"))
    except (ValueError, binascii.Error):
        return False
    if len(decoded) < SALT_LEN:
        return False
    salt = decoded[:SALT_LEN]
    expected = decoded[SALT_LEN:]
    candidate = hashlib.pbkdf2_hmac(HASH_NAME, password.encode("utf-8"), salt, ITERATIONS)
    return secrets.compare_digest(candidate, expected)
