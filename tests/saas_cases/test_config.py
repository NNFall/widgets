from __future__ import annotations

from pathlib import Path

import pytest

from app.config import AppConfig, load_config
from builder_lab.forensics.config import GenerationForensicsConfig


def _database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://kaigo:test@db/kaigo")
    for name in (
        "KAIGO_GENERATION_FORENSICS_ENABLED",
        "KAIGO_GENERATION_FORENSICS_ROOT",
        "KAIGO_GENERATION_FORENSICS_TTL_HOURS",
        "KAIGO_GENERATION_FORENSICS_MAX_BYTES",
        "KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_app_loads_shared_generation_forensics_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    root = tmp_path / "private-forensics"
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "test")
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ENABLED", "true")
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ROOT", str(root))
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_TTL_HOURS", "96")
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_MAX_BYTES", "123456")
    monkeypatch.setenv(
        "KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS",
        " Admin@Example.com,ops@example.com ",
    )

    config = load_config()

    assert config.generation_forensics.enabled is True
    assert config.generation_forensics.root == root
    assert config.generation_forensics.ttl_hours == 96
    assert config.generation_forensics.max_bytes == 123_456
    assert config.generation_forensics.admin_emails == (
        "admin@example.com",
        "ops@example.com",
    )


def test_project_versions_flag_is_strict_and_defaults_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "test")
    monkeypatch.delenv("KAIGO_PROJECT_VERSIONS_ENABLED", raising=False)
    assert load_config().project_versions_enabled is False

    monkeypatch.setenv("KAIGO_PROJECT_VERSIONS_ENABLED", "true")
    assert load_config().project_versions_enabled is True

    monkeypatch.setenv("KAIGO_PROJECT_VERSIONS_ENABLED", "sometimes")
    with pytest.raises(RuntimeError, match="KAIGO_PROJECT_VERSIONS_ENABLED"):
        load_config()


def test_publication_chat_capability_defaults_to_one_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "test")
    monkeypatch.delenv(
        "KAIGO_PUBLICATION_CHAT_CAPABILITY_TTL_SECONDS",
        raising=False,
    )

    assert load_config().publication_chat_capability_ttl_seconds == 3_600


def test_app_config_revalidates_direct_production_forensics_config() -> None:
    unsafe = GenerationForensicsConfig(
        enabled=True,
        root=Path("relative/private"),
        ttl_hours=96,
        max_bytes=1024,
        admin_emails=(),
    )

    with pytest.raises(ValueError, match="absolute|120|allowlist"):
        AppConfig(
            database_url="postgresql://db",
            environment="production",
            generation_forensics=unsafe,
        )


def _production_chat_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "production")
    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("KAIGO_ENTRY_TRUSTED_PROXY_CIDRS", "127.0.0.0/8")
    monkeypatch.setenv("KAIGO_READINESS_TOKEN", "r" * 32)
    monkeypatch.setenv("KAIGO_RELEASE_ID", "release-1")
    monkeypatch.setenv(
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY", "sha256:" + "a" * 64
    )
    monkeypatch.setenv("KAIGO_BUILDER_WORKER_BOOT_ID", "boot-1")
    monkeypatch.setenv("GEMINI_API_KEY", "provider-secret")
    monkeypatch.setenv(
        "KAIGO_PUBLICATION_CHAT_SIGNING_SECRET",
        "a-production-signing-secret-with-32-bytes",
    )


def test_production_requires_public_auth_and_one_complete_oauth_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "production")
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space")
    monkeypatch.delenv("KAIGO_PUBLIC_AUTH_ENABLED", raising=False)
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_ID",
        "YANDEX_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="KAIGO_PUBLIC_AUTH_ENABLED=true"):
        load_config()

    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError, match="at least one complete OAuth provider"):
        load_config()


def test_production_app_config_enforces_public_oauth_contract() -> None:
    with pytest.raises(ValueError, match="KAIGO_PUBLIC_AUTH_ENABLED=true"):
        AppConfig(database_url="postgresql://db", environment="production")

    with pytest.raises(ValueError, match="at least one complete OAuth provider"):
        AppConfig(
            database_url="postgresql://db",
            environment="production",
            public_auth_enabled=True,
            public_base_url="https://kaigo.space",
        )

    config = AppConfig(
        database_url="postgresql://db",
        environment="production",
        public_auth_enabled=True,
        public_base_url="https://kaigo.space",
        google_oauth_client_id="client",
        google_oauth_client_secret="secret",
        entry_trusted_proxy_cidrs=("127.0.0.0/8",),
        readiness_token="r" * 32,
        expected_worker_deployment_id="release-1",
        expected_worker_image_identity="sha256:" + "a" * 64,
        expected_worker_boot_id="boot-1",
    )
    assert config.public_auth_enabled is True


def test_production_requires_private_readiness_identity() -> None:
    kwargs = {
        "database_url": "postgresql://db",
        "environment": "production",
        "public_auth_enabled": True,
        "public_base_url": "https://kaigo.space",
        "google_oauth_client_id": "client",
        "google_oauth_client_secret": "secret",
        "entry_trusted_proxy_cidrs": ("127.0.0.0/8",),
    }

    with pytest.raises(ValueError, match="KAIGO_READINESS_TOKEN"):
        AppConfig(**kwargs)

    with pytest.raises(ValueError, match="at least 32 bytes"):
        AppConfig(
            **kwargs,
            readiness_token="short",
            expected_worker_deployment_id="release-1",
            expected_worker_image_identity="sha256:" + "a" * 64,
            expected_worker_boot_id="boot-1",
        )


@pytest.mark.parametrize(
    ("configured_name", "missing_name"),
    [
        ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"),
        ("GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_ID"),
        ("YANDEX_OAUTH_CLIENT_ID", "YANDEX_OAUTH_CLIENT_SECRET"),
        ("YANDEX_OAUTH_CLIENT_SECRET", "YANDEX_OAUTH_CLIENT_ID"),
    ],
)
def test_production_rejects_incomplete_oauth_provider_pair(
    monkeypatch: pytest.MonkeyPatch,
    configured_name: str,
    missing_name: str,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "production")
    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space")
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_ID",
        "YANDEX_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(configured_name, "configured")

    with pytest.raises(RuntimeError, match=missing_name):
        load_config()


def test_development_keeps_legacy_auth_disabled_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_ENVIRONMENT", "development")
    monkeypatch.delenv("KAIGO_PUBLIC_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("KAIGO_PUBLIC_BASE_URL", raising=False)
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_ID",
        "YANDEX_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.public_auth_enabled is False


@pytest.mark.parametrize("environment", ["prodution", "staging", ""])
def test_app_config_rejects_unknown_environment(environment: str) -> None:
    with pytest.raises(ValueError, match="development, test, or production"):
        AppConfig(database_url="postgresql://db", environment=environment)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_environments_remain_available(environment: str) -> None:
    config = AppConfig(database_url="sqlite+aiosqlite:///:memory:", environment=environment)

    assert config.environment == environment


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
    monkeypatch.setenv("KAIGO_ENTRY_RATE_LIMIT_REQUESTS", "17")
    monkeypatch.setenv("KAIGO_ENTRY_RATE_LIMIT_WINDOW_SECONDS", "75")
    monkeypatch.setenv("KAIGO_ENTRY_MAX_BODY_BYTES", "24576")
    monkeypatch.setenv(
        "KAIGO_ENTRY_TRUSTED_PROXY_CIDRS",
        "127.0.0.0/8, 10.0.0.0/8",
    )
    monkeypatch.setenv("KAIGO_OAUTH_CALLBACK_CONCURRENCY", "3")

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
    assert config.entry_rate_limit_requests == 17
    assert config.entry_rate_limit_window_seconds == 75
    assert config.entry_max_body_bytes == 24_576
    assert config.entry_trusted_proxy_cidrs == (
        "127.0.0.0/8",
        "10.0.0.0/8",
    )
    assert config.oauth_callback_concurrency == 3
    assert "google-secret" not in repr(config)
    assert "yandex-secret" not in repr(config)
    assert "publication-secret" not in repr(config)


def test_production_requires_dedicated_entry_trusted_proxy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_chat_provider(monkeypatch)
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "0")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "0")
    monkeypatch.setenv(
        "KAIGO_PUBLICATION_CHAT_TRUSTED_PROXY_CIDRS", "127.0.0.0/8"
    )
    monkeypatch.delenv("KAIGO_ENTRY_TRUSTED_PROXY_CIDRS", raising=False)

    with pytest.raises(RuntimeError, match="KAIGO_ENTRY_TRUSTED_PROXY_CIDRS"):
        load_config()


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


def test_private_http_provider_proxy_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="absolute https URL"):
        AppConfig(
            database_url="postgresql://db",
            chat_provider_base_url="http://172.19.0.1:8787/private/v1beta",
        )

    config = AppConfig(
        database_url="postgresql://db",
        chat_provider_base_url="http://172.19.0.1:8787/private/v1beta",
        allow_insecure_provider_proxy=True,
    )

    assert config.chat_provider_base_url.startswith("http://172.19.0.1:")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://8.8.8.8/v1beta",
        "http://example.com/v1beta",
    ],
)
def test_insecure_provider_opt_in_never_allows_public_hosts(base_url: str) -> None:
    with pytest.raises(ValueError, match="absolute https URL"):
        AppConfig(
            database_url="postgresql://db",
            chat_provider_base_url=base_url,
            allow_insecure_provider_proxy=True,
        )


def test_load_config_reads_private_provider_proxy_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv(
        "GOOGLE_AI_NATIVE_BASE_URL", "http://172.19.0.1:8787/private/v1beta"
    )
    monkeypatch.setenv("KAIGO_ALLOW_INSECURE_PROVIDER_PROXY", "true")

    assert load_config().allow_insecure_provider_proxy is True


def test_funnel_retention_defaults_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(database_url="sqlite+aiosqlite:///:memory:")
    assert config.funnel_journeys_enabled is False
    assert config.funnel_retention_days == 90
    assert config.funnel_cleanup_interval_seconds == 3600
    assert config.funnel_cleanup_batch_size == 500
    assert config.funnel_cleanup_time_budget_seconds == 5

    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_FUNNEL_JOURNEYS_ENABLED", "true")
    monkeypatch.setenv("KAIGO_FUNNEL_RETENTION_DAYS", "120")
    monkeypatch.setenv("KAIGO_FUNNEL_CLEANUP_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("KAIGO_FUNNEL_CLEANUP_BATCH_SIZE", "250")
    monkeypatch.setenv("KAIGO_FUNNEL_CLEANUP_TIME_BUDGET_SECONDS", "4")
    loaded = load_config()
    assert loaded.funnel_journeys_enabled is True
    assert loaded.funnel_retention_days == 120
    assert loaded.funnel_cleanup_interval_seconds == 600
    assert loaded.funnel_cleanup_batch_size == 250
    assert loaded.funnel_cleanup_time_budget_seconds == 4


@pytest.mark.parametrize("value", [6, 366])
def test_funnel_retention_rejects_unbounded_days(value: int) -> None:
    with pytest.raises(ValueError, match="funnel_retention_days"):
        AppConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            funnel_retention_days=value,
        )


@pytest.mark.parametrize("value", [59, 86_401])
def test_funnel_retention_rejects_unbounded_interval(value: int) -> None:
    with pytest.raises(ValueError, match="funnel_cleanup_interval_seconds"):
        AppConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            funnel_cleanup_interval_seconds=value,
        )


def test_chat_user_rate_limit_prefers_user_name_and_supports_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS", "31")
    monkeypatch.delenv("KAIGO_CHAT_USER_RATE_LIMIT_REQUESTS", raising=False)

    assert load_config().chat_user_rate_limit_requests == 31

    monkeypatch.setenv("KAIGO_CHAT_USER_RATE_LIMIT_REQUESTS", "47")

    assert load_config().chat_user_rate_limit_requests == 47


def test_yookassa_configuration_hides_secret_and_uses_fixed_provider_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop-123")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "super-secret")
    monkeypatch.setenv("YOOKASSA_TEST_MODE", "true")
    monkeypatch.setenv("YOOKASSA_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("YOOKASSA_API_BASE_URL", "https://evil.example")

    config = load_config()

    assert config.yookassa_shop_id == "shop-123"
    assert config.yookassa_secret_key == "super-secret"
    assert config.yookassa_test_mode is True
    assert config.yookassa_timeout_seconds == 17
    assert "super-secret" not in repr(config)
    assert not hasattr(config, "yookassa_api_base_url")


def test_yookassa_receipt_configuration_is_explicit_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert AppConfig(
        database_url="sqlite+aiosqlite:///:memory:"
    ).yookassa_receipts_enabled is False

    _database(monkeypatch)
    monkeypatch.setenv("KAIGO_PUBLIC_BASE_URL", "https://kaigo.space")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop-123")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "super-secret")
    monkeypatch.setenv("YOOKASSA_RECEIPTS_ENABLED", "true")
    monkeypatch.setenv("YOOKASSA_RECEIPT_VAT_CODE", "1")
    monkeypatch.setenv("YOOKASSA_RECEIPT_TAX_SYSTEM_CODE", "1")
    monkeypatch.setenv("YOOKASSA_RECEIPT_PAYMENT_SUBJECT", "service")
    monkeypatch.setenv("YOOKASSA_RECEIPT_PAYMENT_MODE", "full_payment")

    config = load_config()

    assert config.yookassa_receipts_enabled is True
    assert config.yookassa_receipt_vat_code == 1
    assert config.yookassa_receipt_tax_system_code == 1
    assert config.yookassa_receipt_payment_subject == "service"
    assert config.yookassa_receipt_payment_mode == "full_payment"

    monkeypatch.delenv("YOOKASSA_RECEIPT_VAT_CODE")
    with pytest.raises(ValueError, match="vat_code"):
        load_config()


def test_billing_worker_is_safe_and_bounded_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.delenv("KAIGO_BILLING_RENEWALS_ENABLED", raising=False)
    monkeypatch.delenv("KAIGO_BILLING_WORKER_POLL_SECONDS", raising=False)

    config = load_config()

    assert config.billing_renewals_enabled is False
    assert config.billing_worker_poll_seconds == 5

    monkeypatch.setenv("KAIGO_BILLING_WORKER_POLL_SECONDS", "61")
    with pytest.raises(ValueError, match="billing_worker_poll_seconds"):
        load_config()


def test_yookassa_requires_complete_credentials_and_public_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database(monkeypatch)
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop-123")
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="YOOKASSA"):
        load_config()

    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.delenv("KAIGO_PUBLIC_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KAIGO_PUBLIC_BASE_URL"):
        load_config()
