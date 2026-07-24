from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

from .model_config import normalize_thinking_level
from .models import EngineName


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _first_nonblank(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class BuilderLabConfig:
    host: str
    port: int
    allow_remote: bool
    default_engine: EngineName
    direct_model: str
    builder_thinking_level: str
    temperature: float
    max_repairs: int
    enable_antigravity: bool
    gemini_api_key: str | None
    gemini_base_url: str
    antigravity_agent: str
    antigravity_timeout_seconds: int
    antigravity_max_snapshot_bytes: int
    antigravity_max_total_tokens: int
    demo_path: str | None
    demo_dir: str | None
    chat_model: str
    chat_thinking_level: str
    chat_timeout_seconds: int
    visual_critic_model: str
    visual_critic_thinking_level: str
    visual_critic_timeout_seconds: int
    reference_analyzer_model: str
    reference_analyzer_thinking_level: str
    browser_audit_timeout_ms: int
    browser_audit_total_timeout_seconds: int
    chat_session_ttl_seconds: int
    chat_max_sessions: int
    chat_rate_limit_requests: int
    chat_ip_rate_limit_requests: int
    chat_rate_limit_window_seconds: int
    chat_max_requests_per_session: int
    chat_global_concurrency: int
    chat_secure_cookie: bool
    chat_session_secret: str | None
    run_ttl_seconds: int
    max_runs: int
    reference_max_pages: int
    reference_max_depth: int
    reference_timeout_seconds: int
    reference_page_timeout_seconds: int
    reference_max_total_bytes: int
    reference_max_page_bytes: int
    reference_max_retries: int
    reference_max_scroll_steps: int
    reference_scroll_delay_ms: int
    reference_warmup_ms: int
    reference_final_settle_ms: int
    reference_max_scroll_height: int
    reference_trace_ttl_seconds: int
    reference_respect_robots: bool

    def __post_init__(self) -> None:
        if self.chat_session_secret is not None and (
            len(self.chat_session_secret.encode("ascii", "ignore")) < 32
            or re.fullmatch(r"[A-Za-z0-9_-]+", self.chat_session_secret) is None
        ):
            raise ValueError(
                "KAIGO_CHAT_SESSION_SECRET must be at least 32 URL-safe bytes"
            )
        if self.reference_max_page_bytes > self.reference_max_total_bytes:
            raise ValueError(
                "KAIGO_REFERENCE_MAX_PAGE_BYTES cannot exceed "
                "KAIGO_REFERENCE_MAX_TOTAL_BYTES"
            )

    @classmethod
    def from_env(cls) -> "BuilderLabConfig":
        host = os.getenv("KAIGO_BUILDER_LAB_HOST", "127.0.0.1").strip()
        allow_remote = _bool("KAIGO_BUILDER_LAB_ALLOW_REMOTE")
        if not _is_loopback(host) and not allow_remote:
            raise ValueError(
                "KAIGO_BUILDER_LAB_HOST must be a loopback address unless "
                "KAIGO_BUILDER_LAB_ALLOW_REMOTE=true"
            )
        try:
            default_engine = EngineName(
                os.getenv("KAIGO_BUILDER_DEFAULT_ENGINE", "direct").strip().lower()
            )
        except ValueError as exc:
            raise ValueError("KAIGO_BUILDER_DEFAULT_ENGINE is unsupported") from exc
        api_key = _first_nonblank(
            "GEMINI_API_KEY",
            "GOOGLE_AI_API_KEY",
            "GOOGLE_API_KEY",
        )
        direct_model = os.getenv(
            "GEMINI_BUILDER_MODEL", "gemini-3.6-flash"
        ).strip()
        return cls(
            host=host,
            port=_int("KAIGO_BUILDER_LAB_PORT", 8091, 1, 65535),
            allow_remote=allow_remote,
            default_engine=default_engine,
            direct_model=direct_model,
            builder_thinking_level=normalize_thinking_level(
                os.getenv("GEMINI_BUILDER_THINKING_LEVEL", "high")
            ),
            temperature=_float("GEMINI_BUILDER_TEMPERATURE", 0.9, 0, 2),
            max_repairs=_int("GEMINI_BUILDER_MAX_REPAIRS", 3, 0, 4),
            enable_antigravity=_bool("KAIGO_BUILDER_ENABLE_ANTIGRAVITY", True),
            gemini_api_key=api_key,
            gemini_base_url=os.getenv(
                "GOOGLE_AI_NATIVE_BASE_URL",
                "https://generativelanguage.googleapis.com",
            ).strip().rstrip("/"),
            antigravity_agent=os.getenv(
                "GEMINI_ANTIGRAVITY_AGENT", "antigravity-preview-05-2026"
            ).strip(),
            antigravity_timeout_seconds=_int(
                "GEMINI_ANTIGRAVITY_TIMEOUT_SECONDS", 900, 30, 3600
            ),
            antigravity_max_snapshot_bytes=_int(
                "GEMINI_ANTIGRAVITY_MAX_SNAPSHOT_BYTES",
                10 * 1024 * 1024,
                1024,
                100 * 1024 * 1024,
            ),
            antigravity_max_total_tokens=_int(
                "GEMINI_ANTIGRAVITY_MAX_TOTAL_TOKENS",
                500_000,
                10_000,
                2_000_000,
            ),
            demo_path=_first_nonblank("KAIGO_BUILDER_DEMO_PATH"),
            demo_dir=_first_nonblank("KAIGO_BUILDER_DEMO_DIR"),
            chat_model=os.getenv(
                "GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite"
            ).strip(),
            chat_thinking_level=normalize_thinking_level(
                os.getenv("GEMINI_CHAT_THINKING_LEVEL", "medium")
            ),
            chat_timeout_seconds=_int("GEMINI_CHAT_TIMEOUT_SECONDS", 45, 1, 180),
            visual_critic_model=os.getenv(
                "GEMINI_VISUAL_CRITIC_MODEL", "gemini-3.6-flash"
            ).strip(),
            visual_critic_thinking_level=normalize_thinking_level(
                os.getenv("GEMINI_VISUAL_CRITIC_THINKING_LEVEL", "high")
            ),
            visual_critic_timeout_seconds=_int(
                "GEMINI_VISUAL_CRITIC_TIMEOUT_SECONDS", 60, 10, 180
            ),
            reference_analyzer_model=os.getenv(
                "GEMINI_REFERENCE_ANALYZER_MODEL", "gemini-3.6-flash"
            ).strip(),
            reference_analyzer_thinking_level=normalize_thinking_level(
                os.getenv("GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL", "high")
            ),
            browser_audit_timeout_ms=_int(
                "KAIGO_BROWSER_AUDIT_TIMEOUT_MS", 10_000, 1_000, 30_000
            ),
            browser_audit_total_timeout_seconds=_int(
                "KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS", 120, 30, 300
            ),
            chat_session_ttl_seconds=_int(
                "KAIGO_CHAT_SESSION_TTL_SECONDS", 3600, 30, 86400
            ),
            chat_max_sessions=_int("KAIGO_CHAT_MAX_SESSIONS", 500, 1, 10000),
            chat_rate_limit_requests=_int(
                "KAIGO_CHAT_RATE_LIMIT_REQUESTS", 12, 1, 120
            ),
            chat_ip_rate_limit_requests=_int(
                "KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS", 60, 1, 1000
            ),
            chat_rate_limit_window_seconds=_int(
                "KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS", 60, 1, 3600
            ),
            chat_max_requests_per_session=_int(
                "KAIGO_CHAT_MAX_REQUESTS_PER_SESSION", 40, 1, 1000
            ),
            chat_global_concurrency=_int(
                "KAIGO_CHAT_GLOBAL_CONCURRENCY", 4, 1, 32
            ),
            chat_secure_cookie=_bool("KAIGO_CHAT_SECURE_COOKIE", True),
            chat_session_secret=_first_nonblank("KAIGO_CHAT_SESSION_SECRET"),
            run_ttl_seconds=_int("KAIGO_BUILDER_RUN_TTL_SECONDS", 3600, 60, 86400),
            max_runs=_int("KAIGO_BUILDER_MAX_RUNS", 100, 1, 1000),
            reference_max_pages=_int("KAIGO_REFERENCE_MAX_PAGES", 5, 1, 5),
            reference_max_depth=_int("KAIGO_REFERENCE_MAX_DEPTH", 1, 0, 2),
            reference_timeout_seconds=_int(
                "KAIGO_REFERENCE_TIMEOUT_SECONDS", 300, 15, 600
            ),
            reference_page_timeout_seconds=_int(
                "KAIGO_REFERENCE_PAGE_TIMEOUT_SECONDS", 45, 5, 120
            ),
            reference_max_total_bytes=_int(
                "KAIGO_REFERENCE_MAX_TOTAL_BYTES",
                100 * 1024 * 1024,
                1024 * 1024,
                100 * 1024 * 1024,
            ),
            reference_max_page_bytes=_int(
                "KAIGO_REFERENCE_MAX_PAGE_BYTES",
                25 * 1024 * 1024,
                256 * 1024,
                25 * 1024 * 1024,
            ),
            reference_max_retries=_int("KAIGO_REFERENCE_MAX_RETRIES", 1, 0, 2),
            reference_max_scroll_steps=_int(
                "KAIGO_REFERENCE_MAX_SCROLL_STEPS", 40, 1, 60
            ),
            reference_scroll_delay_ms=_int(
                "KAIGO_REFERENCE_SCROLL_DELAY_MS", 750, 600, 1200
            ),
            reference_warmup_ms=_int(
                "KAIGO_REFERENCE_WARMUP_MS", 5000, 1000, 15000
            ),
            reference_final_settle_ms=_int(
                "KAIGO_REFERENCE_FINAL_SETTLE_MS", 1500, 500, 5000
            ),
            reference_max_scroll_height=_int(
                "KAIGO_REFERENCE_MAX_SCROLL_HEIGHT", 50000, 5000, 100000
            ),
            reference_trace_ttl_seconds=_int(
                "KAIGO_REFERENCE_TRACE_TTL_SECONDS", 3600, 300, 86400
            ),
            reference_respect_robots=_bool("KAIGO_REFERENCE_RESPECT_ROBOTS", True),
        )
