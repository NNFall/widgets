from __future__ import annotations

import os
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from dotenv import load_dotenv

from builder_lab.forensics.config import GenerationForensicsConfig

load_dotenv()

_ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "production"})
_PRIVATE_PROVIDER_NETWORKS = tuple(
    ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "127.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)


@dataclass(slots=True)
class AppConfig:
    database_url: str
    host: str = "0.0.0.0"
    port: int = 8080
    environment: str = "development"
    auto_create_schema: bool = False
    public_auth_enabled: bool = False
    public_base_url: str | None = None
    session_cookie_name: str = "kaigo_session"
    session_ttl_seconds: int = 14 * 24 * 60 * 60
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = field(default=None, repr=False)
    yandex_oauth_client_id: str | None = None
    yandex_oauth_client_secret: str | None = field(default=None, repr=False)
    yookassa_shop_id: str | None = None
    yookassa_secret_key: str | None = field(default=None, repr=False)
    yookassa_test_mode: bool = True
    yookassa_timeout_seconds: float = 20
    publication_allow_insecure_origins: bool = False
    publication_chat_signing_secret: str | None = field(default=None, repr=False)
    publication_chat_capability_ttl_seconds: int = 300
    publication_chat_key_rate_limit_requests: int = 120
    publication_chat_ip_rate_limit_requests: int = 60
    publication_chat_trusted_proxy_cidrs: tuple[str, ...] = ()
    entry_rate_limit_requests: int = 20
    entry_rate_limit_window_seconds: int = 60
    entry_max_body_bytes: int = 16_384
    entry_trusted_proxy_cidrs: tuple[str, ...] = ()
    oauth_callback_concurrency: int = 4
    readiness_token: str | None = field(default=None, repr=False)
    expected_worker_deployment_id: str | None = None
    expected_worker_image_identity: str | None = None
    expected_worker_boot_id: str | None = None
    chat_provider_api_key: str | None = field(default=None, repr=False)
    chat_provider_base_url: str = "https://generativelanguage.googleapis.com"
    allow_insecure_provider_proxy: bool = False
    chat_model: str = "gemini-3.5-flash-lite"
    chat_timeout_seconds: float = 45
    chat_session_ttl_seconds: int = 3_600
    chat_max_sessions: int = 500
    chat_rate_limit_requests: int = 12
    chat_user_rate_limit_requests: int = 60
    chat_rate_limit_window_seconds: int = 60
    chat_max_requests_per_session: int = 40
    chat_global_concurrency: int = 4
    chat_input_price_microusd_per_million: int = 0
    chat_output_price_microusd_per_million: int = 0
    generation_forensics: GenerationForensicsConfig = field(
        default_factory=GenerationForensicsConfig.disabled
    )

    def __post_init__(self) -> None:
        self.environment = self.environment.strip().lower()
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            raise ValueError(
                "environment must be development, test, or production"
            )
        if not isinstance(self.generation_forensics, GenerationForensicsConfig):
            raise ValueError(
                "generation_forensics must be a GenerationForensicsConfig"
            )
        self.generation_forensics.validate_for_environment(self.environment)
        if self.environment == "production" and self.auto_create_schema:
            raise ValueError("schema auto-creation is forbidden in production")
        oauth_pairs = (
            (
                "GOOGLE_OAUTH_CLIENT_ID",
                self.google_oauth_client_id,
                "GOOGLE_OAUTH_CLIENT_SECRET",
                self.google_oauth_client_secret,
            ),
            (
                "YANDEX_OAUTH_CLIENT_ID",
                self.yandex_oauth_client_id,
                "YANDEX_OAUTH_CLIENT_SECRET",
                self.yandex_oauth_client_secret,
            ),
        )
        complete_oauth_providers = 0
        for client_name, client_value, secret_name, secret_value in oauth_pairs:
            has_client = bool(client_value and client_value.strip())
            has_secret = bool(secret_value and secret_value.strip())
            if has_client != has_secret:
                missing_name = secret_name if has_client else client_name
                raise ValueError(
                    f"{client_name} and {secret_name} must be configured together; "
                    f"{missing_name} is missing"
                )
            complete_oauth_providers += int(has_client and has_secret)
        if self.environment == "production":
            if not self.public_auth_enabled:
                raise ValueError(
                    "KAIGO_PUBLIC_AUTH_ENABLED=true is required in production"
                )
            if complete_oauth_providers == 0:
                raise ValueError(
                    "at least one complete OAuth provider credential pair is required "
                    "in production"
                )
            if not self.public_base_url:
                raise ValueError("public_base_url is required in production")
            parsed_public_url = urlsplit(self.public_base_url)
            if (
                parsed_public_url.scheme != "https"
                or not parsed_public_url.netloc
                or parsed_public_url.path not in {"", "/"}
            ):
                raise ValueError(
                    "public_base_url must be an absolute https origin in production"
                )
            if not self.entry_trusted_proxy_cidrs:
                raise ValueError(
                    "KAIGO_ENTRY_TRUSTED_PROXY_CIDRS is required in production"
                )
            readiness_fields = (
                ("KAIGO_READINESS_TOKEN", self.readiness_token),
                ("KAIGO_RELEASE_ID", self.expected_worker_deployment_id),
                (
                    "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY",
                    self.expected_worker_image_identity,
                ),
                ("KAIGO_BUILDER_WORKER_BOOT_ID", self.expected_worker_boot_id),
            )
            missing = [name for name, value in readiness_fields if not value]
            if missing:
                raise ValueError(
                    "production worker readiness identity is incomplete: "
                    + ", ".join(missing)
                )
            if len(self.readiness_token.encode("utf-8")) < 32:
                raise ValueError("readiness_token must be at least 32 bytes in production")
        self.chat_model = self.chat_model.strip()
        self.chat_provider_base_url = self.chat_provider_base_url.strip().rstrip("/")
        if not self.chat_model:
            raise ValueError("chat_model must not be blank")
        if (
            self.environment == "production"
            and self.chat_provider_api_key
            and (
                not self.publication_chat_signing_secret
                or len(self.publication_chat_signing_secret.strip().encode("utf-8"))
                < 32
            )
        ):
            raise ValueError(
                "publication_chat_signing_secret must be at least 32 bytes in production"
            )
        trusted_proxy_cidrs: list[str] = []
        for raw_cidr in self.publication_chat_trusted_proxy_cidrs:
            try:
                trusted_proxy_cidrs.append(str(ip_network(raw_cidr, strict=False)))
            except ValueError as error:
                raise ValueError(
                    f"publication trusted proxy CIDR is invalid: {raw_cidr}"
                ) from error
        self.publication_chat_trusted_proxy_cidrs = tuple(trusted_proxy_cidrs)
        entry_trusted_proxy_cidrs: list[str] = []
        for raw_cidr in self.entry_trusted_proxy_cidrs:
            try:
                entry_trusted_proxy_cidrs.append(
                    str(ip_network(raw_cidr, strict=False))
                )
            except ValueError as error:
                raise ValueError(
                    f"entry trusted proxy CIDR is invalid: {raw_cidr}"
                ) from error
        self.entry_trusted_proxy_cidrs = tuple(entry_trusted_proxy_cidrs)
        parsed = urlsplit(self.chat_provider_base_url)
        private_http_proxy = (
            parsed.scheme == "http"
            and self.allow_insecure_provider_proxy
            and _is_private_provider_host(parsed.hostname)
        )
        if not parsed.netloc or (parsed.scheme != "https" and not private_http_proxy):
            raise ValueError("chat_provider_base_url must be an absolute https URL")
        if (
            isinstance(self.chat_timeout_seconds, bool)
            or not 1 <= self.chat_timeout_seconds <= 180
        ):
            raise ValueError("chat_timeout_seconds must be between 1 and 180")
        if (
            isinstance(self.yookassa_timeout_seconds, bool)
            or not 1 <= self.yookassa_timeout_seconds <= 60
        ):
            raise ValueError("yookassa_timeout_seconds must be between 1 and 60")
        bounds = (
            ("chat_session_ttl_seconds", self.chat_session_ttl_seconds, 30, 86_400),
            ("chat_max_sessions", self.chat_max_sessions, 1, 10_000),
            ("chat_rate_limit_requests", self.chat_rate_limit_requests, 1, 120),
            (
                "chat_user_rate_limit_requests",
                self.chat_user_rate_limit_requests,
                1,
                1_000,
            ),
            (
                "chat_rate_limit_window_seconds",
                self.chat_rate_limit_window_seconds,
                1,
                3_600,
            ),
            (
                "chat_max_requests_per_session",
                self.chat_max_requests_per_session,
                1,
                1_000,
            ),
            ("chat_global_concurrency", self.chat_global_concurrency, 1, 32),
            (
                "publication_chat_capability_ttl_seconds",
                self.publication_chat_capability_ttl_seconds,
                30,
                3_600,
            ),
            (
                "publication_chat_key_rate_limit_requests",
                self.publication_chat_key_rate_limit_requests,
                1,
                10_000,
            ),
            (
                "publication_chat_ip_rate_limit_requests",
                self.publication_chat_ip_rate_limit_requests,
                1,
                1_000,
            ),
            ("entry_rate_limit_requests", self.entry_rate_limit_requests, 1, 1_000),
            (
                "entry_rate_limit_window_seconds",
                self.entry_rate_limit_window_seconds,
                1,
                3_600,
            ),
            ("entry_max_body_bytes", self.entry_max_body_bytes, 1_024, 1_048_576),
            ("oauth_callback_concurrency", self.oauth_callback_concurrency, 1, 32),
        )
        for name, value, minimum, maximum in bounds:
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        for name, value in (
            (
                "chat_input_price_microusd_per_million",
                self.chat_input_price_microusd_per_million,
            ),
            (
                "chat_output_price_microusd_per_million",
                self.chat_output_price_microusd_per_million,
            ),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must not be negative")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_private_provider_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        return False
    return any(address in network for network in _PRIVATE_PROVIDER_NETWORKS)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error


def _env_int_with_fallback(primary: str, fallback: str, default: int) -> int:
    if os.getenv(primary) is not None:
        return _env_int(primary, default)
    return _env_int(fallback, default)


def _chat_price(name: str, *, required: bool) -> int:
    raw = os.getenv(name)
    if required and (raw is None or not raw.strip()):
        raise RuntimeError(
            f"{name} is required when the production chat provider is enabled"
        )
    if raw is None or not raw.strip():
        return 0
    return _env_int(name, 0)


def _first_nonblank(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _public_base_url(enabled: bool) -> str | None:
    raw = os.getenv("KAIGO_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not raw:
        if enabled:
            raise RuntimeError(
                "KAIGO_PUBLIC_BASE_URL is required when public auth is enabled"
            )
        return None

    parsed = urlsplit(raw)
    if not parsed.netloc or parsed.scheme != "https" or parsed.path not in {"", "/"}:
        raise RuntimeError("KAIGO_PUBLIC_BASE_URL must be an absolute https origin")
    return raw


def load_config() -> AppConfig:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8080"))
    environment = os.getenv("KAIGO_ENVIRONMENT", "development")
    chat_provider_api_key = _first_nonblank(
        "GEMINI_API_KEY", "GOOGLE_AI_API_KEY", "GOOGLE_API_KEY"
    )
    chat_prices_required = (
        environment.strip().lower() == "production"
        and chat_provider_api_key is not None
    )
    public_auth_enabled = _env_flag("KAIGO_PUBLIC_AUTH_ENABLED")
    production = environment.strip().lower() == "production"
    google_oauth_client_id = _first_nonblank("GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret = _first_nonblank("GOOGLE_OAUTH_CLIENT_SECRET")
    yandex_oauth_client_id = _first_nonblank("YANDEX_OAUTH_CLIENT_ID")
    yandex_oauth_client_secret = _first_nonblank("YANDEX_OAUTH_CLIENT_SECRET")
    oauth_pairs = (
        (
            "GOOGLE_OAUTH_CLIENT_ID",
            google_oauth_client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET",
            google_oauth_client_secret,
        ),
        (
            "YANDEX_OAUTH_CLIENT_ID",
            yandex_oauth_client_id,
            "YANDEX_OAUTH_CLIENT_SECRET",
            yandex_oauth_client_secret,
        ),
    )
    for client_name, client_value, secret_name, secret_value in oauth_pairs:
        if bool(client_value) != bool(secret_value):
            missing_name = secret_name if client_value else client_name
            raise RuntimeError(
                f"{client_name} and {secret_name} must be configured together; "
                f"{missing_name} is missing"
            )
    if production and not public_auth_enabled:
        raise RuntimeError("KAIGO_PUBLIC_AUTH_ENABLED=true is required in production")
    if production and not any(
        client and secret for _, client, _, secret in oauth_pairs
    ):
        raise RuntimeError(
            "at least one complete OAuth provider credential pair is required in production"
        )
    entry_trusted_proxy_cidrs = tuple(
        part.strip()
        for part in os.getenv("KAIGO_ENTRY_TRUSTED_PROXY_CIDRS", "").split(",")
        if part.strip()
    )
    if production and not entry_trusted_proxy_cidrs:
        raise RuntimeError(
            "KAIGO_ENTRY_TRUSTED_PROXY_CIDRS is required in production"
        )
    yookassa_shop_id = _first_nonblank("YOOKASSA_SHOP_ID")
    yookassa_secret_key = _first_nonblank("YOOKASSA_SECRET_KEY")
    if bool(yookassa_shop_id) != bool(yookassa_secret_key):
        raise RuntimeError(
            "YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY must be configured together"
        )
    billing_enabled = bool(yookassa_shop_id and yookassa_secret_key)
    return AppConfig(
        database_url=database_url,
        host=host,
        port=port,
        environment=environment,
        auto_create_schema=_env_flag("KAIGO_AUTO_CREATE_SCHEMA"),
        public_auth_enabled=public_auth_enabled,
        public_base_url=_public_base_url(public_auth_enabled or billing_enabled),
        session_cookie_name=os.getenv("KAIGO_SESSION_COOKIE_NAME", "kaigo_session"),
        session_ttl_seconds=int(
            os.getenv("KAIGO_SESSION_TTL_SECONDS", str(14 * 24 * 60 * 60))
        ),
        google_oauth_client_id=google_oauth_client_id,
        google_oauth_client_secret=google_oauth_client_secret,
        yandex_oauth_client_id=yandex_oauth_client_id,
        yandex_oauth_client_secret=yandex_oauth_client_secret,
        yookassa_shop_id=yookassa_shop_id,
        yookassa_secret_key=yookassa_secret_key,
        yookassa_test_mode=_env_flag("YOOKASSA_TEST_MODE", True),
        yookassa_timeout_seconds=_env_int("YOOKASSA_TIMEOUT_SECONDS", 20),
        publication_allow_insecure_origins=_env_flag(
            "KAIGO_PUBLICATION_ALLOW_INSECURE_ORIGINS"
        ),
        publication_chat_signing_secret=_first_nonblank(
            "KAIGO_PUBLICATION_CHAT_SIGNING_SECRET"
        ),
        publication_chat_capability_ttl_seconds=_env_int(
            "KAIGO_PUBLICATION_CHAT_CAPABILITY_TTL_SECONDS", 300
        ),
        publication_chat_key_rate_limit_requests=_env_int(
            "KAIGO_PUBLICATION_CHAT_KEY_RATE_LIMIT_REQUESTS", 120
        ),
        publication_chat_ip_rate_limit_requests=_env_int(
            "KAIGO_PUBLICATION_CHAT_IP_RATE_LIMIT_REQUESTS", 60
        ),
        publication_chat_trusted_proxy_cidrs=tuple(
            part.strip()
            for part in os.getenv(
                "KAIGO_PUBLICATION_CHAT_TRUSTED_PROXY_CIDRS", ""
            ).split(",")
            if part.strip()
        ),
        entry_rate_limit_requests=_env_int("KAIGO_ENTRY_RATE_LIMIT_REQUESTS", 20),
        entry_rate_limit_window_seconds=_env_int(
            "KAIGO_ENTRY_RATE_LIMIT_WINDOW_SECONDS", 60
        ),
        entry_max_body_bytes=_env_int("KAIGO_ENTRY_MAX_BODY_BYTES", 16_384),
        entry_trusted_proxy_cidrs=entry_trusted_proxy_cidrs,
        oauth_callback_concurrency=_env_int("KAIGO_OAUTH_CALLBACK_CONCURRENCY", 4),
        readiness_token=_first_nonblank("KAIGO_READINESS_TOKEN"),
        expected_worker_deployment_id=_first_nonblank("KAIGO_RELEASE_ID"),
        expected_worker_image_identity=_first_nonblank(
            "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY"
        ),
        expected_worker_boot_id=_first_nonblank("KAIGO_BUILDER_WORKER_BOOT_ID"),
        chat_provider_api_key=chat_provider_api_key,
        chat_provider_base_url=os.getenv(
            "GOOGLE_AI_NATIVE_BASE_URL",
            "https://generativelanguage.googleapis.com",
        ),
        allow_insecure_provider_proxy=_env_flag(
            "KAIGO_ALLOW_INSECURE_PROVIDER_PROXY"
        ),
        chat_model=os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite"),
        chat_timeout_seconds=_env_int("GEMINI_CHAT_TIMEOUT_SECONDS", 45),
        chat_session_ttl_seconds=_env_int("KAIGO_CHAT_SESSION_TTL_SECONDS", 3_600),
        chat_max_sessions=_env_int("KAIGO_CHAT_MAX_SESSIONS", 500),
        chat_rate_limit_requests=_env_int("KAIGO_CHAT_RATE_LIMIT_REQUESTS", 12),
        chat_user_rate_limit_requests=_env_int_with_fallback(
            "KAIGO_CHAT_USER_RATE_LIMIT_REQUESTS",
            "KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS",
            60,
        ),
        chat_rate_limit_window_seconds=_env_int(
            "KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS", 60
        ),
        chat_max_requests_per_session=_env_int(
            "KAIGO_CHAT_MAX_REQUESTS_PER_SESSION", 40
        ),
        chat_global_concurrency=_env_int("KAIGO_CHAT_GLOBAL_CONCURRENCY", 4),
        chat_input_price_microusd_per_million=_chat_price(
            "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION",
            required=chat_prices_required,
        ),
        chat_output_price_microusd_per_million=_chat_price(
            "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION",
            required=chat_prices_required,
        ),
        generation_forensics=GenerationForensicsConfig.from_env(
            environment=environment
        ),
    )
