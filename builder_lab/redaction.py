from __future__ import annotations

import re


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{16,}\b")
_NAMED_SECRET = re.compile(
    r"(?i)\b(gemini_api_key|google_ai_api_key|google_api_key|api[_ -]?key|key|"
    r"authorization|password|passwd|secret|token|access[_ -]?token|"
    r"client[_ -]?secret|chat[_ -]?system[_ -]?prompt)"
    r"\b[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|api_key|token|access_token|password|secret)=)"
    r"[^&#\s]+"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]+")
_PRIVATE_UNIX_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:root|home|Users|var|etc|app|srv|opt|tmp)/"
    r"[^\s\"'<>]*"
)


def redact_diagnostic(value: object, *, limit: int = 4000) -> str | None:
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return None
    text = _CONTROL_CHARACTERS.sub(" ", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    text = _GOOGLE_API_KEY.sub("[REDACTED]", text)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    text = _NAMED_SECRET.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _WINDOWS_ABSOLUTE_PATH.sub("[REDACTED_PATH]", text)
    text = _PRIVATE_UNIX_PATH.sub("[REDACTED_PATH]", text)
    text = " ".join(text.split())
    return text[:limit].strip() or None


__all__ = ["redact_diagnostic"]
