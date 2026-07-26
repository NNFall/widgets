import json
from pathlib import Path

import pytest

from tools.codex_telegram_bridge.config import ConfigError, load_config


THREAD_ID = "019f9e1b-fb04-7482-b62d-cee4c051131b"


def _write_config(path: Path, **overrides: object) -> None:
    payload = {
        "allowed_user_ids": [101],
        "chat_bindings": {"202": THREAD_ID},
        "data_dir": "%LOCALAPPDATA%\\KaigoCodexTelegramBridge",
        "codex_home": "%USERPROFILE%\\.codex",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_config_expands_paths_and_reads_token(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path)

    config = load_config(
        path,
        environ={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "USERPROFILE": str(tmp_path / "user"),
            "TELEGRAM_BOT_TOKEN": "secret-token",
        },
    )

    assert config.telegram_token == "secret-token"
    assert config.allowed_user_ids == frozenset({101})
    assert config.chat_bindings == {202: THREAD_ID}
    assert config.data_dir == tmp_path / "local" / "KaigoCodexTelegramBridge"
    assert config.codex_home == tmp_path / "user" / ".codex"
    assert config.codex_command == "codex.cmd"
    assert config.retention_days == 30


def test_load_config_requires_token_environment_variable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path)

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(path, environ={"LOCALAPPDATA": str(tmp_path), "USERPROFILE": str(tmp_path)})


def test_load_config_rejects_custom_token_environment_name(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, telegram_token_env="CUSTOM_SECRET")

    with pytest.raises(ConfigError, match="telegram_token_env"):
        load_config(
            path,
            environ={
                "LOCALAPPDATA": str(tmp_path),
                "USERPROFILE": str(tmp_path),
                "TELEGRAM_BOT_TOKEN": "secret-token",
                "CUSTOM_SECRET": "other-secret",
            },
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"allowed_user_ids": [0]}, "allowed_user_ids"),
        ({"chat_bindings": {"not-an-id": THREAD_ID}}, "chat_bindings"),
        ({"chat_bindings": {"202": "not-a-uuid"}}, "Codex thread"),
        ({"poll_timeout_seconds": 0}, "poll_timeout_seconds"),
    ],
)
def test_load_config_rejects_invalid_values(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "config.json"
    _write_config(path, **overrides)

    with pytest.raises(ConfigError, match=message):
        load_config(
            path,
            environ={
                "LOCALAPPDATA": str(tmp_path),
                "USERPROFILE": str(tmp_path),
                "TELEGRAM_BOT_TOKEN": "secret-token",
            },
        )
