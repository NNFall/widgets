from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

import pytest

from builder_lab.redaction import redact_diagnostic, redact_private_data


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _assert_private_fragments_absent(value: object, *fragments: str) -> None:
    serialized = _serialized(value).casefold()
    if any(fragment.casefold() in serialized for fragment in fragments):
        pytest.fail("private material leaked from recursive redaction")


def test_recursive_redaction_masks_nested_secrets_and_pii() -> None:
    value = {
        "headers": {
            "Authorization": "Bearer secret-value-123",
            "Cookie": "session=abc123456789",
        },
        "customer": {
            "email": "person@example.com",
            "phones": ["+7 (927) 000-00-00"],
        },
        "provider": {"raw": "token=secret-value-123"},
    }

    redacted = redact_private_data(value)

    _assert_private_fragments_absent(
        redacted,
        "secret-value",
        "person@example.com",
        "927",
        "abc123456789",
    )
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["Cookie"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("api_key=private-api-value-123456", "private-api-value"),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature1234567890",
            "eyjhb",
        ),
        ("sk-proj-abcdefghijklmnopqrstuvxyz", "abcdefghijkl"),
        ("https://operator:private-password@example.com/x", "private-password"),
        ("postgresql://operator:private-password@localhost/db", "private-password"),
        ("https://example.com/x?access_token=private-query-value", "private-query-value"),
        (r"trace at C:\Users\operator\private\trace.log", "operator"),
        ("trace at /home/operator/private/trace.log", "operator"),
        ("contact person@example.com", "person@example.com"),
        ("call +44 20 7946 0958", "7946"),
        ("call +376 123 456", "123 456"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("Cookie: sid=one; refresh=two", "refresh=two"),
        ("Set-Cookie: sid=one; HttpOnly", "sid=one"),
        ("contact пользователь@пример.рф", "пользователь@пример.рф"),
        ("contact user@xn--e1afmkfd.xn--p1ai", "user@xn--e1afmkfd.xn--p1ai"),
        (
            "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxMjMifQ.",
            "eyJhbGciOiJub25lIn0",
        ),
        (
            "-----BEGIN PRIVATE KEY-----\nprivate-pem-material\n-----END PRIVATE KEY-----",
            "private-pem-material",
        ),
        (r"trace at \\server\share\private\trace.log", "server"),
        (r"trace at \\?\C:\private\trace.log", "private"),
        ("trace at C:/Users/operator/private/trace.log", "operator"),
    ],
)
def test_recursive_redaction_masks_sensitive_string_patterns(
    value: str,
    fragment: str,
) -> None:
    _assert_private_fragments_absent(redact_private_data(value), fragment)


@pytest.mark.parametrize(
    "key",
    [
        "Set-Cookie",
        "password",
        "client secret",
        "refresh-token",
        "api_key",
    ],
)
def test_recursive_redaction_masks_entire_sensitive_key_value_without_stringifying(
    key: str,
) -> None:
    class MustNotStringify:
        def __str__(self) -> str:
            raise AssertionError("sensitive values must not be stringified")

    assert redact_private_data({key: MustNotStringify()}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("key", ["very-long-private-field_password", "clientSecret"])
def test_recursive_redaction_classifies_sensitive_keys_before_truncating(
    key: str,
) -> None:
    result = redact_private_data(
        {key: "opaque-private-value"},
        max_string_chars=20,
    )

    assert list(result.values()) == ["[REDACTED]"]


def test_recursive_redaction_bounds_sensitive_key_classification_work() -> None:
    class TrapFullStringOperations(str):
        def strip(self, *_args, **_kwargs):
            raise AssertionError("full key must not be normalized")

    key = TrapFullStringOperations("x" * 100_000 + "_password")

    result = redact_private_data({key: "opaque-private-value"})

    assert isinstance(result, dict)
    assert list(result.values()) == ["[REDACTED]"]


def test_recursive_redaction_preserves_generic_ui_key_but_masks_private_key() -> None:
    result = redact_private_data(
        {
            "key": "launcher",
            "publicKey": "public-material",
            "privateKey": "opaque-private-material",
        }
    )

    assert result == {
        "key": "launcher",
        "publicKey": "public-material",
        "privateKey": "[REDACTED]",
    }


@pytest.mark.parametrize(
    "key",
    [
        "YOOKASSA_SECRET_KEY",
        "OPENAI_API_KEY",
        "AWS_ACCESS_KEY",
        "vendorPrivateKey",
        "providerClientSecret",
        "providerRefreshToken",
        "DATABASE_PASSWORD",
        "SERVICE_CREDENTIALS",
    ],
)
def test_recursive_redaction_masks_vendor_prefixed_credential_keys(key: str) -> None:
    assert redact_private_data({key: "opaque-private-value"}) == {
        key: "[REDACTED]"
    }


@pytest.mark.parametrize(
    "value",
    [
        "YOOKASSA_SECRET_KEY=test_private_yookassa_value",
        'OPENAI_API_KEY="private-openai-value"',
        "AWS_ACCESS_KEY: private-aws-value",
        "providerClientSecret='private-client-value'",
        "providerRefreshToken=private-refresh-value",
        "DATABASE_PASSWORD=private-database-value",
        "SERVICE_CREDENTIALS=private-credential-value",
    ],
)
def test_recursive_redaction_masks_vendor_prefixed_inline_credentials(
    value: str,
) -> None:
    _assert_private_fragments_absent(redact_private_data(value), "private")


def test_recursive_redaction_masks_entire_long_inline_credentials() -> None:
    long_assignment = "token=" + "x" * 10_000 + " suffix=safe"
    long_openai_token = "sk-" + "a" * 500

    assert redact_private_data(long_assignment) == "token=[REDACTED] suffix=safe"
    assert redact_private_data(long_openai_token) == "[REDACTED]"


def test_recursive_redaction_masks_entire_long_jwt_and_url_userinfo() -> None:
    long_jwt = (
        "eyJ"
        + "a" * 1_100
        + "."
        + "b" * 9_000
        + "."
        + "c" * 3_000
    )
    long_url_password = "p" * 3_000
    private_url = (
        "https://operator:"
        + long_url_password
        + "@example.com/private"
    )

    assert redact_private_data(long_jwt) == "[REDACTED]"
    assert redact_private_data(private_url) == (
        "https://[REDACTED]@example.com/private"
    )


def test_recursive_redaction_fails_closed_for_credentials_crossing_scan_boundary() -> None:
    public_prefix = "p" * 60_000 + " "
    boundary_url = (
        public_prefix
        + "https://operator:"
        + "u" * 20_000
        + "@example.com/private"
    )
    boundary_jwt = (
        public_prefix
        + "eyJ"
        + "j" * 20_000
        + ".payload.signature"
    )

    redacted_url = redact_private_data(boundary_url)
    redacted_jwt = redact_private_data(boundary_jwt)

    assert isinstance(redacted_url, str)
    assert isinstance(redacted_jwt, str)
    assert "operator" not in redacted_url
    assert "u" * 128 not in redacted_url
    assert "j" * 128 not in redacted_jwt
    assert "[REDACTED]" in redacted_url
    assert "[REDACTED]" in redacted_jwt
    assert redacted_url.endswith("[TRUNCATED]")
    assert redacted_jwt.endswith("[TRUNCATED]")


def test_recursive_redaction_coerces_hostile_string_subclasses_safely() -> None:
    class HostileString(str):
        def __getitem__(self, _key):
            raise RuntimeError("private string exception detail")

        def __str__(self) -> str:
            raise RuntimeError("private string conversion detail")

    secret = HostileString("token=private-inline-value")
    secret_key = HostileString("YOOKASSA_SECRET_KEY")

    assert redact_private_data(secret) == "token=[REDACTED]"
    assert redact_private_data({secret_key: "private-mapping-value"}) == {
        "YOOKASSA_SECRET_KEY": "[REDACTED]"
    }


def test_recursive_redaction_preserves_noncredential_key_names() -> None:
    assert redact_private_data(
        {
            "key": "launcher",
            "theme_key": "primary",
            "publicKey": "public-material",
            "monkey": "banana",
            "note": "theme_key=primary publicKey=public-material",
        }
    ) == {
        "key": "launcher",
        "theme_key": "primary",
        "publicKey": "public-material",
        "monkey": "banana",
        "note": "theme_key=primary publicKey=public-material",
    }


def test_recursive_redaction_preserves_json_order_and_public_scalars() -> None:
    value = {
        "first": 7,
        "enabled": True,
        "ratio": 1.5,
        "missing": None,
        "tuple": ("safe", 9),
    }

    redacted = redact_private_data(value)

    assert list(redacted) == list(value)
    assert redacted == {
        "first": 7,
        "enabled": True,
        "ratio": 1.5,
        "missing": None,
        "tuple": ["safe", 9],
    }


def test_recursive_redaction_is_bounded_and_does_not_follow_cycles() -> None:
    value: list[object] = []
    value.append(value)

    assert redact_private_data(value) == ["[TRUNCATED]"]


def test_recursive_redaction_enforces_non_overridable_safe_depth() -> None:
    value: dict[str, object] = {"leaf": "safe"}
    for _ in range(100):
        value = {"nested": value}

    result = redact_private_data(value, max_depth=10_000)

    assert "[TRUNCATED]" in _serialized(result)


def test_recursive_redaction_fails_closed_for_malformed_containers_and_keys() -> None:
    class BrokenMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("private mapping detail")

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("private iterator detail")

        def __len__(self) -> int:
            return 1

    class BrokenKey:
        def __hash__(self) -> int:
            return 1

        def __str__(self) -> str:
            raise RuntimeError("private key detail")

    assert redact_private_data(BrokenMapping()) == "[TRUNCATED]"
    assert redact_private_data({BrokenKey(): "opaque-private-value"}) == "[TRUNCATED]"
    assert redact_private_data({b"binary-private-key": "opaque-private-value"}) == "[TRUNCATED]"


def test_recursive_redaction_marks_normalized_key_collisions_and_mapping_overflow() -> None:
    assert redact_private_data({1: "first", "1": "second"}) == "[TRUNCATED]"
    assert redact_private_data(
        {"[TRUNCATED]": "real value", "second": "not visited"},
        max_items=1,
    ) == "[TRUNCATED]"


def test_recursive_redaction_bounds_depth_items_strings_and_serialized_bytes() -> None:
    deep = {"level": {"level": {"level": "safe"}}}
    many = list(range(20))
    long_text = "x" * 500
    oversized = ["y" * 64_000 for _ in range(20)]

    depth_result = redact_private_data(deep, max_depth=1)
    item_result = redact_private_data(many, max_items=4)
    string_result = redact_private_data(long_text, max_string_chars=32)
    oversized_result = redact_private_data(oversized)

    assert "[TRUNCATED]" in _serialized(depth_result)
    assert item_result == [0, 1, 2, 3, "[TRUNCATED]"]
    assert isinstance(string_result, str)
    assert len(string_result) <= 32
    assert string_result.endswith("[TRUNCATED]")
    assert len(_serialized(oversized_result).encode("utf-8")) <= 1_000_000


def test_recursive_redaction_distinguishes_phones_from_timestamps_and_request_ids() -> None:
    value = {
        "phone": "+7 (927) 000-00-00",
        "timestamp": "2026-07-30 10:15:20",
        "compact_timestamp": "202607301015",
        "request_id": "request-1234-5678-9012",
    }

    assert redact_private_data(value) == {
        "phone": "[REDACTED]",
        "timestamp": "2026-07-30 10:15:20",
        "compact_timestamp": "202607301015",
        "request_id": "request-1234-5678-9012",
    }


def test_recursive_redaction_masks_private_paths_without_masking_http_routes() -> None:
    result = redact_private_data(
        {
            "route": "GET https://example.com/app/private HTTP/1.1",
            "relative_route": "GET /app/private HTTP/1.1",
            "private_path": "trace at /app/private/trace.log",
        }
    )

    assert result == {
        "route": "GET https://example.com/app/private HTTP/1.1",
        "relative_route": "GET /app/private HTTP/1.1",
        "private_path": "trace at [REDACTED_PATH]",
    }


def test_recursive_redaction_applies_incremental_output_budget() -> None:
    value = ["x" * 64_000 for _ in range(1_000)]

    result = redact_private_data(value)
    encoded = _serialized(result).encode("utf-8")

    assert len(encoded) <= 1_000_000
    assert isinstance(result, list)
    assert result[-1] == "[TRUNCATED]"
    assert len(result) < len(value)


def test_recursive_redaction_replaces_binary_and_control_characters() -> None:
    result = redact_private_data({"blob": b"private", "text": "a\x00b\x1fc"})

    assert result == {"blob": "[BINARY]", "text": "a b c"}


@pytest.mark.parametrize(
    "value",
    ["\ud800", 10**5_000],
    ids=["lone-surrogate", "huge-integer"],
)
def test_recursive_redaction_returns_safe_marker_for_unencodable_scalars(
    value: object,
) -> None:
    assert redact_private_data(value) == "[TRUNCATED]"


def test_redact_diagnostic_keeps_legacy_facade_and_delegates_string_redaction() -> None:
    assert redact_diagnostic(None) is None
    assert redact_diagnostic("  safe\x00  text  ") == "safe text"
    assert redact_diagnostic("token=private-secret-value", limit=12) == "token=[REDAC"

    class BrokenString:
        def __str__(self) -> str:
            raise RuntimeError("must stay private")

    assert redact_diagnostic(BrokenString()) is None


@pytest.mark.parametrize("prefix_length", [1_000, 3_000, 3_900])
def test_redact_diagnostic_fails_closed_for_credentials_crossing_scan_boundary(
    prefix_length: int,
) -> None:
    public_prefix = "p" * prefix_length + " "
    boundary_url = (
        public_prefix
        + "https://operator:"
        + "u" * 13_000
        + "@example.com/private"
    )
    boundary_jwt = (
        public_prefix
        + "eyJ"
        + "j" * 13_000
        + ".payload.signature"
    )

    redacted_url = redact_diagnostic(boundary_url)
    redacted_jwt = redact_diagnostic(boundary_jwt)

    assert redacted_url is not None
    assert redacted_jwt is not None
    assert "operator" not in redacted_url
    assert "u" * 64 not in redacted_url
    assert "j" * 64 not in redacted_jwt
    assert "[REDACTED]" in redacted_url
    assert "[REDACTED]" in redacted_jwt
