from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
BINARY = "[BINARY]"

_SAFE_MAX_DEPTH = 12
_SAFE_MAX_ITEMS = 1_000
_SAFE_MAX_STRING_CHARS = 64_000
_MAX_STRING_SCAN_CHARS = 72_192
_MAX_SERIALIZED_BYTES = 1_000_000
_INCREMENTAL_BYTE_BUDGET = 900_000
_MAX_JSON_INTEGER = (1 << 63) - 1
_TRUNCATION_LOOKAHEAD = 8_192

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_AUTHORIZATION_HEADER = re.compile(
    r"(?im)\b(authorization|proxy-authorization)\s*:[^\r\n]*"
)
_COOKIE_HEADER = re.compile(r"(?im)\b(set-cookie|cookie)\s*:[^\r\n]*")
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>(?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY)-----"
    r"[\s\S]*?(?:-----END (?P=label)-----|$)"
)
_BASIC_SECRET = re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{4,}={0,2}")
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{16,}")
_OPENAI_STYLE_TOKEN = re.compile(r"(?i)\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\."
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"
    r"(?![A-Za-z0-9_-])"
)
_EMAIL = re.compile(
    r"(?<![\w.!#$%&'*+/=?^_`{|}~-])"
    r"[\w.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[\w-]{1,63}\.){1,10}[\w-]{2,63}"
    r"(?![\w-])",
    re.IGNORECASE,
)
_PHONE_CANDIDATE = re.compile(
    r"(?<![\w-])\+?\d[\d\s().-]{5,30}\d(?![\w-])"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]{0,255})"
    r"\b[\"']?\s*[:=]\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_NAMED_SECRET = re.compile(
    r"(?i)\b(gemini_api_key|google_ai_api_key|google_api_key|api[_ -]?key|"
    r"apiKey|authorization|proxy[_ -]?authorization|password|passwd|secret|"
    r"token|access[_ -]?token|accessToken|refresh[_ -]?token|refreshToken|"
    r"client[_ -]?secret|clientSecret|private[_ -]?key|privateKey|cookie|"
    r"set[_ -]?cookie|chat[_ -]?system[_ -]?prompt)"
    r"\b[\"']?\s*[:=]\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_URL_USERINFO = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]{0,31}://)"
    r"[^/\s:@]+:[^/\s@]+@"
)
_PARTIAL_URL_USERINFO_AT_SCAN_END = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]{0,31}://)"
    r"[^/\s:@]+:[^/\s@]*$"
)
_PARTIAL_JWT_AT_SCAN_END = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+"
    r"(?:\.[A-Za-z0-9_-]*){0,2}$"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|api_key|token|access_token|refresh_token|password|"
    r"secret|client_secret)=)[^&#\s]*"
)
_WINDOWS_EXTENDED_PATH = re.compile(
    r"\\\\\?\\(?:[A-Za-z]:\\|UNC\\[^\\/\s]+\\[^\\/\s]+\\?)"
    r"[^\s\"'<>|]{0,4096}"
)
_WINDOWS_UNC_PATH = re.compile(
    r"\\\\(?!\?\\)[^\\/\s\"'<>|]{1,255}\\"
    r"[^\\/\s\"'<>|]{1,255}(?:\\[^\s\"'<>|]{0,4096})?"
)
_WINDOWS_DRIVE_PATH = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]{0,4096}"
)
_PRIVATE_UNIX_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:root|home|Users|var|etc|app|srv|opt|tmp)/"
    r"[^\s\"'<>]{0,4096}"
)
_HTTP_ORIGIN_SUFFIX = re.compile(r"(?i)https?://[^/\s]+$")
_HTTP_METHOD_SUFFIX = re.compile(
    r"(?i)(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+$"
)
_COMPACT_DATE_PREFIX = re.compile(r"^(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "chat_system_prompt",
        "client_secret",
        "cookie",
        "gemini_api_key",
        "google_ai_api_key",
        "google_api_key",
        "password",
        "passwd",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "set_cookie",
        "token",
    }
)
_COMPACT_SENSITIVE_KEYS = frozenset(item.replace("_", "") for item in _SENSITIVE_KEYS)
_SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_token",
    "_api_key",
    "_secret_key",
    "_access_key",
    "_private_key",
    "_client_secret",
    "_access_token",
    "_refresh_token",
    "_auth_token",
    "_session_token",
    "_credential",
    "_credentials",
)
_COMPACT_SENSITIVE_KEY_SUFFIXES = (
    "password",
    "passwd",
    "secretkey",
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "sessiontoken",
    "credential",
    "credentials",
)
_BUDGET_EXHAUSTED = object()


class _ByteBudget:
    __slots__ = ("remaining",)

    def __init__(self) -> None:
        self.remaining = _INCREMENTAL_BYTE_BUDGET

    def charge_raw(self, size: int) -> bool:
        if size < 0 or size > self.remaining:
            return False
        self.remaining -= size
        return True

    def charge_json(self, value: JsonValue) -> bool:
        try:
            size = len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (UnicodeEncodeError, ValueError, OverflowError):
            return False
        return self.charge_raw(size)


def _normalized_key(value: str) -> str:
    return re.sub(
        r"_+",
        "_",
        value.strip().casefold().replace("-", "_").replace(" ", "_"),
    )


def _plain_string(value: str) -> str | None:
    try:
        return str.__str__(value)
    except Exception:
        return None


def _is_sensitive_key(value: str) -> bool:
    value = _plain_string(value)
    if value is None:
        return True
    sample = (
        value[:512]
        if len(value) <= 512
        else f"{value[:256]}_{value[-256:]}"
    )
    normalized = _normalized_key(sample)
    compact = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_KEYS
        or compact in _COMPACT_SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
        or compact.endswith(_COMPACT_SENSITIVE_KEY_SUFFIXES)
    )


def _redact_credential_assignment(match: re.Match[str]) -> str:
    key = match.group(1)
    if not _is_sensitive_key(key):
        return match.group(0)
    return f"{key}={REDACTED}"


def _redact_phone(match: re.Match[str]) -> str:
    candidate = match.group(0)
    compact = candidate.strip()
    if _COMPACT_DATE_PREFIX.match(compact):
        return candidate
    digits = re.sub(r"\D", "", compact)
    if compact.isdigit() and len(digits) in {10, 12, 14}:
        try:
            date = datetime.strptime(digits[:8], "%Y%m%d")
        except ValueError:
            pass
        else:
            if 1900 <= date.year <= 2100:
                return candidate
    minimum_digits = 7 if compact.startswith("+") else 10
    return REDACTED if minimum_digits <= len(digits) <= 15 else candidate


def _redact_unix_path(match: re.Match[str], *, source: str) -> str:
    prefix = source[max(0, match.start() - 2_048) : match.start()]
    if _HTTP_ORIGIN_SUFFIX.search(prefix) or _HTTP_METHOD_SUFFIX.search(prefix):
        return match.group(0)
    return "[REDACTED_PATH]"


def _redact_string(value: str) -> str:
    text = _PEM_PRIVATE_KEY.sub(REDACTED, value)
    text = _AUTHORIZATION_HEADER.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _COOKIE_HEADER.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _CONTROL_CHARACTERS.sub(" ", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    text = _GOOGLE_API_KEY.sub(REDACTED, text)
    text = _OPENAI_STYLE_TOKEN.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _BASIC_SECRET.sub("Basic [REDACTED]", text)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(_redact_credential_assignment, text)
    text = _NAMED_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _WINDOWS_EXTENDED_PATH.sub("[REDACTED_PATH]", text)
    text = _WINDOWS_UNC_PATH.sub("[REDACTED_PATH]", text)
    text = _WINDOWS_DRIVE_PATH.sub("[REDACTED_PATH]", text)
    text = _PRIVATE_UNIX_PATH.sub(
        lambda match: _redact_unix_path(match, source=text),
        text,
    )
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE_CANDIDATE.sub(_redact_phone, text)
    return " ".join(text.split())


def _redact_partial_credentials_at_scan_end(value: str) -> str:
    text = _PARTIAL_URL_USERINFO_AT_SCAN_END.sub(r"\1[REDACTED]", value)
    return _PARTIAL_JWT_AT_SCAN_END.sub(REDACTED, text)


def _truncate_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    if limit <= len(TRUNCATED):
        return TRUNCATED[:limit]
    return value[: limit - len(TRUNCATED)] + TRUNCATED


def _bounded_private_string(value: str, *, output_limit: int) -> str:
    value = _plain_string(value)
    if value is None:
        return TRUNCATED
    scan_limit = min(
        _MAX_STRING_SCAN_CHARS,
        max(4_096, output_limit + _TRUNCATION_LOOKAHEAD),
    )
    source = value[:scan_limit]
    if len(value) > scan_limit:
        source = _redact_partial_credentials_at_scan_end(source)
    redacted = _redact_string(source)
    if len(value) > scan_limit:
        safe_limit = max(0, output_limit - len(TRUNCATED))
        return _truncate_string(redacted, safe_limit) + TRUNCATED
    return _truncate_string(redacted, output_limit)


def _json_key(raw_key: object) -> str | None:
    if isinstance(raw_key, str):
        return _plain_string(raw_key)
    if isinstance(raw_key, (bytes, bytearray, memoryview)):
        return None
    if raw_key is None or isinstance(raw_key, (bool, int, float)):
        if isinstance(raw_key, int) and not isinstance(raw_key, bool):
            if abs(raw_key) > _MAX_JSON_INTEGER:
                return None
        if isinstance(raw_key, float) and not math.isfinite(raw_key):
            return None
        try:
            return str(raw_key)
        except Exception:
            return None
    return None


def redact_private_data(
    value: object,
    *,
    max_depth: int = 12,
    max_items: int = 1_000,
    max_string_chars: int = 64_000,
) -> JsonValue:
    """Return a bounded JSON-safe copy with secrets and common PII removed."""

    if max_depth < 0 or max_items < 0 or max_string_chars < 0:
        raise ValueError("redaction bounds must not be negative")
    depth_limit = min(max_depth, _SAFE_MAX_DEPTH)
    item_limit = min(max_items, _SAFE_MAX_ITEMS)
    string_limit = min(max_string_chars, _SAFE_MAX_STRING_CHARS)
    budget = _ByteBudget()
    active_containers: set[int] = set()
    item_count = 0

    def marker() -> JsonValue | object:
        return TRUNCATED if budget.charge_json(TRUNCATED) else _BUDGET_EXHAUSTED

    def visit(candidate: object, depth: int) -> JsonValue | object:
        nonlocal item_count
        if depth > depth_limit:
            return marker()
        if candidate is None or isinstance(candidate, (bool, int)):
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                if abs(candidate) > _MAX_JSON_INTEGER:
                    return marker()
            return candidate if budget.charge_json(candidate) else _BUDGET_EXHAUSTED
        if isinstance(candidate, float):
            if not math.isfinite(candidate):
                return marker()
            return candidate if budget.charge_json(candidate) else _BUDGET_EXHAUSTED
        if isinstance(candidate, str):
            public = _bounded_private_string(candidate, output_limit=string_limit)
            return public if budget.charge_json(public) else _BUDGET_EXHAUSTED
        if isinstance(candidate, (bytes, bytearray, memoryview)):
            return BINARY if budget.charge_json(BINARY) else _BUDGET_EXHAUSTED

        if isinstance(candidate, Mapping):
            identity = id(candidate)
            if identity in active_containers:
                return marker()
            if not budget.charge_raw(2):
                return _BUDGET_EXHAUSTED
            active_containers.add(identity)
            result: dict[str, JsonValue] = {}
            try:
                try:
                    iterator = iter(candidate.items())
                    for raw_key, raw_value in iterator:
                        if item_count >= item_limit:
                            return marker()
                        item_count += 1
                        key = _json_key(raw_key)
                        if key is None:
                            return marker()
                        sensitive = _is_sensitive_key(key)
                        public_key = _bounded_private_string(
                            key,
                            output_limit=string_limit,
                        )
                        if public_key in result:
                            return marker()
                        if not budget.charge_raw(2) or not budget.charge_json(public_key):
                            return marker()
                        if sensitive:
                            public_value: JsonValue | object = (
                                REDACTED
                                if budget.charge_json(REDACTED)
                                else _BUDGET_EXHAUSTED
                            )
                        else:
                            public_value = visit(raw_value, depth + 1)
                        if public_value is _BUDGET_EXHAUSTED:
                            return marker()
                        result[public_key] = public_value
                except Exception:
                    return marker()
            finally:
                active_containers.remove(identity)
            return result

        if isinstance(candidate, (list, tuple)):
            identity = id(candidate)
            if identity in active_containers:
                return marker()
            if not budget.charge_raw(2):
                return _BUDGET_EXHAUSTED
            active_containers.add(identity)
            result_list: list[JsonValue] = []
            try:
                try:
                    for item in candidate:
                        if item_count >= item_limit:
                            truncated = marker()
                            if truncated is not _BUDGET_EXHAUSTED:
                                result_list.append(truncated)
                            break
                        item_count += 1
                        if not budget.charge_raw(1):
                            truncated = marker()
                            if truncated is not _BUDGET_EXHAUSTED:
                                result_list.append(truncated)
                            break
                        public_item = visit(item, depth + 1)
                        if public_item is _BUDGET_EXHAUSTED:
                            truncated = marker()
                            if truncated is not _BUDGET_EXHAUSTED:
                                result_list.append(truncated)
                            break
                        result_list.append(public_item)
                except Exception:
                    return marker()
            finally:
                active_containers.remove(identity)
            return result_list

        return marker()

    result = visit(value, 0)
    if result is _BUDGET_EXHAUSTED:
        return TRUNCATED
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError, OverflowError):
        return TRUNCATED
    return result if len(encoded) <= _MAX_SERIALIZED_BYTES else TRUNCATED


def redact_diagnostic(value: object, *, limit: int = 4000) -> str | None:
    if value is None:
        return None
    try:
        text = _plain_string(value) if isinstance(value, str) else str(value)
    except Exception:
        return None
    if text is None:
        return None
    output_limit = min(max(0, limit), _SAFE_MAX_STRING_CHARS)
    scan_limit = min(
        _MAX_STRING_SCAN_CHARS,
        max(4_096, output_limit + _TRUNCATION_LOOKAHEAD),
    )
    source = text[:scan_limit]
    if len(text) > scan_limit:
        source = _redact_partial_credentials_at_scan_end(source)
    text = _redact_string(source)
    return text[:output_limit].strip() or None


__all__ = [
    "BINARY",
    "JsonScalar",
    "JsonValue",
    "REDACTED",
    "TRUNCATED",
    "redact_diagnostic",
    "redact_private_data",
]
