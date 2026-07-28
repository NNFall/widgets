from __future__ import annotations

import pytest

from app.config import load_config


def _database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://kaigo:test@db/kaigo")


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
