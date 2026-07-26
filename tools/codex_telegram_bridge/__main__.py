from __future__ import annotations

import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .codex_runner import CodexRunner
from .config import ConfigError, load_config
from .instance_lock import AlreadyRunningError, SingleInstanceLock
from .service import BridgeService
from .store import BridgeStore
from .telegram_api import TelegramClient


def _default_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ConfigError("LOCALAPPDATA is not set")
    return Path(local_app_data) / "KaigoCodexTelegramBridge" / "config.json"


def _configure_logging(data_dir: Path) -> logging.Logger:
    data_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("codex_telegram_bridge")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        data_dir / "bridge.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram transport for local Codex tasks")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config or _default_config_path())
    except ConfigError as exc:
        parser.error(str(exc))
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if args.check_config:
        print(
            f"Configuration is valid: {len(config.allowed_user_ids)} allowed user(s), "
            f"{len(config.chat_bindings)} default binding(s)."
        )
        return 0
    logger = _configure_logging(config.data_dir)
    try:
        with SingleInstanceLock(config.data_dir):
            store = BridgeStore(config.data_dir / "bridge.sqlite3")
            telegram = TelegramClient(config.telegram_token, api_base=config.telegram_api_base)
            runner = CodexRunner(
                codex_command=config.codex_command,
                codex_home=config.codex_home,
                data_dir=config.data_dir / "codex-output",
                timeout_seconds=config.turn_timeout_seconds,
            )
            service = BridgeService(config, telegram, runner, store, logger=logger)
            try:
                service.run_forever()
            except KeyboardInterrupt:
                logger.info("Telegram Codex bridge stopped")
            finally:
                store.close()
    except AlreadyRunningError as exc:
        logger.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
