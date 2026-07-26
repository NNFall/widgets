from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when bridge configuration is absent or unsafe."""


@dataclass(frozen=True)
class BridgeConfig:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    chat_bindings: dict[int, str]
    data_dir: Path
    codex_home: Path
    codex_command: str = "codex.cmd"
    poll_timeout_seconds: int = 30
    turn_timeout_seconds: int = 3600
    retry_delay_seconds: float = 5.0
    retention_days: int = 30
    telegram_api_base: str = "https://api.telegram.org"


_WINDOWS_ENV = re.compile(r"%([^%]+)%")


def validate_thread_id(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError("Codex thread ID must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ConfigError("Codex thread ID must be a valid UUID") from exc
    if str(parsed) != value.lower():
        raise ConfigError("Codex thread ID must use the canonical UUID format")
    return str(parsed)


def _expand_path(value: object, environ: Mapping[str, str], field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty path")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environ:
            raise ConfigError(f"Environment variable %{name}% used by {field} is not set")
        return environ[name]

    return Path(_WINDOWS_ENV.sub(replace, value)).expanduser()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field} must be a positive integer")
    return value


def load_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> BridgeConfig:
    env = os.environ if environ is None else environ
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file was not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Configuration file is not valid JSON: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Configuration root must be a JSON object")

    if "telegram_token_env" in raw:
        raise ConfigError(
            "telegram_token_env is not supported; use TELEGRAM_BOT_TOKEN so the secret "
            "can always be removed from the Codex child environment"
        )
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("Environment variable TELEGRAM_BOT_TOKEN is not set")

    raw_users = raw.get("allowed_user_ids")
    if not isinstance(raw_users, list) or not raw_users:
        raise ConfigError("allowed_user_ids must contain at least one Telegram user ID")
    allowed_user_ids = frozenset(_positive_int(value, "allowed_user_ids") for value in raw_users)

    raw_bindings = raw.get("chat_bindings", {})
    if not isinstance(raw_bindings, dict):
        raise ConfigError("chat_bindings must be a JSON object")
    bindings: dict[int, str] = {}
    for raw_chat_id, raw_thread_id in raw_bindings.items():
        try:
            chat_id = int(raw_chat_id)
        except (TypeError, ValueError) as exc:
            raise ConfigError("chat_bindings keys must be Telegram chat IDs") from exc
        if chat_id == 0:
            raise ConfigError("chat_bindings keys must be non-zero Telegram chat IDs")
        bindings[chat_id] = validate_thread_id(raw_thread_id)

    poll_timeout = _positive_int(raw.get("poll_timeout_seconds", 30), "poll_timeout_seconds")
    turn_timeout = _positive_int(raw.get("turn_timeout_seconds", 3600), "turn_timeout_seconds")
    retention_days = _positive_int(raw.get("retention_days", 30), "retention_days")
    retry_delay = raw.get("retry_delay_seconds", 5.0)
    if isinstance(retry_delay, bool) or not isinstance(retry_delay, (int, float)) or retry_delay <= 0:
        raise ConfigError("retry_delay_seconds must be a positive number")

    codex_command = raw.get("codex_command", "codex.cmd")
    if not isinstance(codex_command, str) or not codex_command.strip():
        raise ConfigError("codex_command must be a non-empty string")
    api_base = raw.get("telegram_api_base", "https://api.telegram.org")
    if not isinstance(api_base, str) or not api_base.startswith("https://"):
        raise ConfigError("telegram_api_base must be an HTTPS URL")

    return BridgeConfig(
        telegram_token=token,
        allowed_user_ids=allowed_user_ids,
        chat_bindings=bindings,
        data_dir=_expand_path(raw.get("data_dir"), env, "data_dir"),
        codex_home=_expand_path(raw.get("codex_home"), env, "codex_home"),
        codex_command=codex_command,
        poll_timeout_seconds=poll_timeout,
        turn_timeout_seconds=turn_timeout,
        retry_delay_seconds=float(retry_delay),
        retention_days=retention_days,
        telegram_api_base=api_base.rstrip("/"),
    )
