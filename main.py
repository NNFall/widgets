import asyncio
import logging

from wrappers import admin_bot, telegram_bot
from wrappers import web_app_aiohttp as web_app
from core import database

logger = logging.getLogger(__name__)

def configure_logging() -> None:
    """Настраивает простое и единообразное логирование для всех модулей."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for noisy_logger in ("aiohttp.access", "aiogram.dispatcher", "telethon"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


async def main() -> None:
    """Главная точка входа: подготавливает инфраструктуру и запускает сервисы."""
    configure_logging()

    from core import config

    logger.info("Режим работы: %s", config.settings.operation_mode.value)
    logger.info(
        "VSEGPT_API_KEY: %s",
        "установлен" if config.settings.VSEGPT_API_KEY else "НЕ УСТАНОВЛЕН",
    )
    logger.info(
        "TELEGRAM_API_ID: %s",
        "установлен" if config.settings.TELEGRAM_API_ID else "НЕ УСТАНОВЛЕН",
    )
    logger.info(
        "TELEGRAM_API_HASH: %s",
        "установлен" if config.settings.TELEGRAM_API_HASH else "НЕ УСТАНОВЛЕН",
    )

    await database.init_db()
    logger.info("База данных успешно инициализирована.")
    logger.info("Запускаем ботов и веб-сервер...")

    await asyncio.gather(
        # telegram_bot.start(),  # Запускает пользовательского бота (Telethon)
        # admin_bot.start(),     # Запускает админ-бота (Aiogram)
        web_app.start(),         # Запускает веб-сервер (aiohttp)
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа остановлена пользователем.")
