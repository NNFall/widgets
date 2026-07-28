from __future__ import annotations

import pytest

from app.config import load_config


def _database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://kaigo:test@db/kaigo")


def _production_chat_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "production")
    monkeypatch.setenv("GEMINI_API_KEY", "provider-secret")
    monkeypatch.setenv(
        "KAIGO_PUBLICATION_CHAT_SIGNING_SECRET",
        "a-production-signing-secret-with-32-bytes",
    )


def test_public_auth_requires_absolute_https_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    monkeypatch.delenv("KAIGO_PUBLIC_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="KAIGO_PUBLIC_BASE_URL"):
        load_config()

    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "http://kaigo.space")
    with pytest.raises(RuntimeError, match="https"):
        load_config()


def test_public_auth_configuration_is_loaded_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space/")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", "yandex-client")
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_SECRET", "yandex-secret")
    monkeypatch.setenv("KAIGO_SESSION_COOKIE_NAME", "custom_session")
    monkeypatch.setenv("KAIGO_SESSION_TTL_SECONDS", "7200")
    monkeypatch.setenv("KAIGO_PUBLICATION_CHAT_SIGNING_SECRET", "publication-secret")
    monkeypatch.setenv("KAIGO_PUBLICATION_CHAT_CAPABILITY_TTL_SECONDS", "180")
    monkeypatch.setenv("KAIGO_PUBLICATION_CHAT_KEY_RATE_LIMIT_REQUESTS", "90")
    monkeypatch.setenv("KAIGO_PUBLICATION_CHAT_IP_RATE_LIMIT_REQUESTS", "30")
    monkeypatch.setenv(
        "KAIGO_PUBLICATION_CHAT_TRUSTED_PROXY_CIDRS",
        "127.0.0.0/8, 10.0.0.0/8",
    )

    config = load_config()

    assert config.public_base_url == "https://kaigo.space"
    assert config.public_auth_enabled is True
    assert config.session_cookie_name == "custom_session"
    assert config.session_ttl_seconds == 7200
    assert config.google_oauth_client_id == "google-client"
    assert config.yandex_oauth_client_id == "yandex-client"
    assert config.publication_chat_capability_ttl_seconds == 180
    assert config.publication_chat_key_rate_limit_requests == 90
    assert config.publication_chat_ip_rate_limit_requests == 30
    assert config.publication_chat_trusted_proxy_cidrs == (
        "127.0.0.0/8",
        "10.0.0.0/8",
    )
    assert "google-secret" not in repr(config)
    assert "yandex-secret" not in repr(config)
    assert "publication-secret" not in repr(config)


def test_public_auth_can_stay_disabled_for_legacy_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.delenv("KAIGO_PUBLIC_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("KAIGO_PUBLIC_BASE_URL", raising=False)

    config = load_config()

    assert config.public_auth_enabled is False
    assert config.public_base_url is None
    assert config.session_cookie_name == "kaigo_session"


@pytest.mark.parametrize(
    "missing_name",
    [
        "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION",
        "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION",
    ],
)
def test_production_chat_provider_requires_explicit_prices(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    _production_chat_provider(monkeypatch)
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "0")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "0")
    monkeypatch.delenv(missing_name, raising=False)

    with pytest.raises(RuntimeError, match=missing_name):
        load_config()


def test_production_chat_provider_accepts_explicit_zero_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_chat_provider(monkeypatch)
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "0")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "0")

    config = load_config()

    assert config.chat_input_price_microusd_per_million == 0
    assert config.chat_output_price_microusd_per_million == 0


def test_chat_user_rate_limit_prefers_user_name_and_supports_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS", "31")
    monkeypatch.delenv("KAIGO_CHAT_USER_RATE_LIMIT_REQUESTS", raising=False)

    assert load_config().chat_user_rate_limit_requests == 31

    monkeypatch.setenv("KAIGO_CHAT_USER_RATE_LIMIT_REQUESTS", "47")

    assert load_config().chat_user_rate_limit_requests == 47
