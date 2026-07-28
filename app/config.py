from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class AppConfig:
    database_url: str
    host: str = '0.0.0.0'
    port: int = 8080
    environment: str = 'development'
    public_auth_enabled: bool = False
    public_base_url: str | None = None
    session_cookie_name: str = 'kaigo_session'
    session_ttl_seconds: int = 14 * 24 * 60 * 60
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = field(default=None, repr=False)
    yandex_oauth_client_id: str | None = None
    yandex_oauth_client_secret: str | None = field(default=None, repr=False)
    publication_allow_insecure_origins: bool = False
    chat_provider_api_key: str | None = field(default=None, repr=False)
    chat_provider_base_url: str = 'https://generativelanguage.googleapis.com'
    chat_model: str = 'gemini-3.5-flash-lite'
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

    def __post_init__(self) -> None:
        self.chat_model = self.chat_model.strip()
        self.chat_provider_base_url = self.chat_provider_base_url.strip().rstrip('/')
        if not self.chat_model:
            raise ValueError('chat_model must not be blank')
        parsed = urlsplit(self.chat_provider_base_url)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('chat_provider_base_url must be an absolute https URL')
        if (
            isinstance(self.chat_timeout_seconds, bool)
            or not 1 <= self.chat_timeout_seconds <= 180
        ):
            raise ValueError('chat_timeout_seconds must be between 1 and 180')
        bounds = (
            ('chat_session_ttl_seconds', self.chat_session_ttl_seconds, 30, 86_400),
            ('chat_max_sessions', self.chat_max_sessions, 1, 10_000),
            ('chat_rate_limit_requests', self.chat_rate_limit_requests, 1, 120),
            ('chat_user_rate_limit_requests', self.chat_user_rate_limit_requests, 1, 1_000),
            ('chat_rate_limit_window_seconds', self.chat_rate_limit_window_seconds, 1, 3_600),
            ('chat_max_requests_per_session', self.chat_max_requests_per_session, 1, 1_000),
            ('chat_global_concurrency', self.chat_global_concurrency, 1, 32),
        )
        for name, value, minimum, maximum in bounds:
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be between {minimum} and {maximum}')
        for name, value in (
            ('chat_input_price_microusd_per_million', self.chat_input_price_microusd_per_million),
            ('chat_output_price_microusd_per_million', self.chat_output_price_microusd_per_million),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f'{name} must not be negative')


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f'{name} must be an integer') from error


def _first_nonblank(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, '').strip()
        if value:
            return value
    return None


def _public_base_url(enabled: bool) -> str | None:
    raw = os.getenv('KAIGO_PUBLIC_BASE_URL', '').strip().rstrip('/')
    if not raw:
        if enabled:
            raise RuntimeError('KAIGO_PUBLIC_BASE_URL is required when public auth is enabled')
        return None

    parsed = urlsplit(raw)
    if not parsed.netloc or parsed.scheme != 'https' or parsed.path not in {'', '/'}:
        raise RuntimeError('KAIGO_PUBLIC_BASE_URL must be an absolute https origin')
    return raw


def load_config() -> AppConfig:
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL environment variable is required')
    host = os.getenv('APP_HOST', '0.0.0.0')
    port = int(os.getenv('APP_PORT', '8080'))
    public_auth_enabled = _env_flag('KAIGO_PUBLIC_AUTH_ENABLED')
    return AppConfig(
        database_url=database_url,
        host=host,
        port=port,
        environment=os.getenv('KAIGO_ENVIRONMENT', 'development'),
        public_auth_enabled=public_auth_enabled,
        public_base_url=_public_base_url(public_auth_enabled),
        session_cookie_name=os.getenv('KAIGO_SESSION_COOKIE_NAME', 'kaigo_session'),
        session_ttl_seconds=int(os.getenv('KAIGO_SESSION_TTL_SECONDS', str(14 * 24 * 60 * 60))),
        google_oauth_client_id=os.getenv('GOOGLE_OAUTH_CLIENT_ID'),
        google_oauth_client_secret=os.getenv('GOOGLE_OAUTH_CLIENT_SECRET'),
        yandex_oauth_client_id=os.getenv('YANDEX_OAUTH_CLIENT_ID'),
        yandex_oauth_client_secret=os.getenv('YANDEX_OAUTH_CLIENT_SECRET'),
        publication_allow_insecure_origins=_env_flag(
            'KAIGO_PUBLICATION_ALLOW_INSECURE_ORIGINS'
        ),
        chat_provider_api_key=_first_nonblank(
            'GEMINI_API_KEY', 'GOOGLE_AI_API_KEY', 'GOOGLE_API_KEY'
        ),
        chat_provider_base_url=os.getenv(
            'GOOGLE_AI_NATIVE_BASE_URL',
            'https://generativelanguage.googleapis.com',
        ),
        chat_model=os.getenv('GEMINI_CHAT_MODEL', 'gemini-3.5-flash-lite'),
        chat_timeout_seconds=_env_int('GEMINI_CHAT_TIMEOUT_SECONDS', 45),
        chat_session_ttl_seconds=_env_int('KAIGO_CHAT_SESSION_TTL_SECONDS', 3_600),
        chat_max_sessions=_env_int('KAIGO_CHAT_MAX_SESSIONS', 500),
        chat_rate_limit_requests=_env_int('KAIGO_CHAT_RATE_LIMIT_REQUESTS', 12),
        chat_user_rate_limit_requests=_env_int('KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS', 60),
        chat_rate_limit_window_seconds=_env_int('KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS', 60),
        chat_max_requests_per_session=_env_int('KAIGO_CHAT_MAX_REQUESTS_PER_SESSION', 40),
        chat_global_concurrency=_env_int('KAIGO_CHAT_GLOBAL_CONCURRENCY', 4),
        chat_input_price_microusd_per_million=_env_int(
            'GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION', 0
        ),
        chat_output_price_microusd_per_million=_env_int(
            'GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION', 0
        ),
    )
