from __future__ import annotations

import re

from app.auth.tokens import issue_token, token_digest


def test_session_token_is_random_and_only_digest_matches() -> None:
    raw, digest = issue_token()

    assert raw != digest
    assert token_digest(raw) == digest
    assert len(raw) >= 43
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_session_tokens_are_unique() -> None:
    first_raw, first_digest = issue_token()
    second_raw, second_digest = issue_token()

    assert first_raw != second_raw
    assert first_digest != second_digest

