from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

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
    temperature: float
    max_repairs: int
    enable_antigravity: bool
    gemini_api_key: str | None
    gemini_base_url: str
    antigravity_agent: str
    antigravity_timeout_seconds: int
    antigravity_max_snapshot_bytes: int
    demo_path: str | None
    run_ttl_seconds: int
    max_runs: int

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
        return cls(
            host=host,
            port=_int("KAIGO_BUILDER_LAB_PORT", 8091, 1, 65535),
            allow_remote=allow_remote,
            default_engine=default_engine,
            direct_model=os.getenv("GEMINI_BUILDER_MODEL", "gemini-3.5-flash").strip(),
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
            demo_path=_first_nonblank("KAIGO_BUILDER_DEMO_PATH"),
            run_ttl_seconds=_int("KAIGO_BUILDER_RUN_TTL_SECONDS", 3600, 60, 86400),
            max_runs=_int("KAIGO_BUILDER_MAX_RUNS", 100, 1, 1000),
        )
