from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$"
)
_DEFAULT_ROOT = "/app/data/generation-forensics"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024 * 1024


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _normalize_admin_emails(raw: str) -> tuple[str, ...]:
    values = {
        item.strip().casefold()
        for item in raw.split(",")
        if item.strip()
    }
    for value in values:
        if len(value) > 254 or _EMAIL.fullmatch(value) is None:
            raise ValueError(
                "KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS contains an invalid email"
            )
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True, repr=False)
class GenerationForensicsConfig:
    enabled: bool
    root: Path
    ttl_hours: int
    max_bytes: int
    admin_emails: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser()
        emails = tuple(sorted({item.strip().casefold() for item in self.admin_emails}))
        if isinstance(self.enabled, bool) is False:
            raise ValueError("enabled must be a boolean")
        if isinstance(self.ttl_hours, bool) or not 72 <= self.ttl_hours <= 120:
            raise ValueError("forensics TTL must be between 72 and 120 hours")
        if isinstance(self.max_bytes, bool) or not 1 <= self.max_bytes <= 1 << 50:
            raise ValueError("forensics max_bytes must be between 1 and 1125899906842624")
        for value in emails:
            if len(value) > 254 or _EMAIL.fullmatch(value) is None:
                raise ValueError("forensics admin allowlist contains an invalid email")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "admin_emails", emails)

    def __repr__(self) -> str:
        return (
            "GenerationForensicsConfig("
            f"enabled={self.enabled!r}, ttl_hours={self.ttl_hours!r}, "
            f"max_bytes={self.max_bytes!r}, "
            f"admin_count={len(self.admin_emails)!r})"
        )

    @classmethod
    def disabled(cls) -> GenerationForensicsConfig:
        return cls(
            enabled=False,
            root=Path(_DEFAULT_ROOT),
            ttl_hours=120,
            max_bytes=_DEFAULT_MAX_BYTES,
            admin_emails=(),
        )

    def validate_for_environment(self, environment: str) -> None:
        normalized_environment = environment.strip().casefold()
        if normalized_environment not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        if normalized_environment == "production" and self.enabled:
            if not self.root.is_absolute():
                raise ValueError(
                    "production generation forensics root must be absolute"
                )
            if self.ttl_hours != 120:
                raise ValueError(
                    "production generation forensics TTL must be exactly 120 hours"
                )
            if not self.admin_emails:
                raise ValueError(
                    "production generation forensics admin allowlist must not be empty"
                )

    @classmethod
    def from_env(cls, *, environment: str) -> GenerationForensicsConfig:
        normalized_environment = environment.strip().casefold()
        enabled = _env_bool("KAIGO_GENERATION_FORENSICS_ENABLED", False)
        root = Path(
            os.getenv("KAIGO_GENERATION_FORENSICS_ROOT", _DEFAULT_ROOT).strip()
        ).expanduser()
        ttl_hours = _env_int("KAIGO_GENERATION_FORENSICS_TTL_HOURS", 120)
        max_bytes = _env_int(
            "KAIGO_GENERATION_FORENSICS_MAX_BYTES", _DEFAULT_MAX_BYTES
        )
        admin_emails = _normalize_admin_emails(
            os.getenv("KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS", "")
        )
        config = cls(
            enabled=enabled,
            root=root,
            ttl_hours=ttl_hours,
            max_bytes=max_bytes,
            admin_emails=admin_emails,
        )
        config.validate_for_environment(normalized_environment)
        return config
