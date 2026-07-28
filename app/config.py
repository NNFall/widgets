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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


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
    )
