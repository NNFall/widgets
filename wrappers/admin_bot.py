# /wrappers/admin_bot.py

import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, BotCommand
from . import telegram_bot
from core import config, database as db, utils as core_utils
from .filters import IsSubscribedFilter
from core import config as core_config, database as db, utils as core_utils
# Инициализируем бота и диспетчер
bot = Bot(token=config.settings.ADMIN_BOT_TOKEN)
dp = Dispatcher()

# Создаем экземпляр нашего фильтра
is_subscribed_filter = IsSubscribedFilter(config.settings.ADMIN_CHANNEL_ID)

# --- Обработчики команд ---
async def set_bot_commands(bot: Bot):
    """Устанавливает список команд, которые будут видны в меню Telegram."""
    commands = [
        BotCommand(command="start", description="▶️ Перезапустить бота"),
        BotCommand(command="stopuser", description="🚫 Остановить диалог"),
        BotCommand(command="startuser", description="✅ Возобновить диалог"),
        BotCommand(command="clearhistory", description="🗑️ Очистить историю"),
        BotCommand(command="updateprompt", description="🔄 Обновить промпт"),
        # НОВАЯ КОМАНДА В СПИСКЕ
        BotCommand(command="setmodel", description="⚙️ Изменить модель ИИ"),
        BotCommand(command="setmode", description="🚦 Изменить режим работы бота"),
    ]
    await bot.set_my_commands(commands)
# Команда /start
@dp.message(CommandStart(), is_subscribed_filter)
async def command_start(message: Message):
    await message.answer(f"Привет, администратор {message.from_user.full_name}!")

async def resolve_user(arg: str) -> int | None:
    """
    Преобразует аргумент (ID или username) в user_id.
    Сначала проверяет, является ли arg числом.
    Затем пытается найти ID через "живой" поиск Telethon.
    В качестве запасного варианта ищет в локальной БД.
    """
    if arg.isdigit():
        return int(arg)
    
    # 1. Основной метод: "живой" поиск через API Telethon
    user_id = await telegram_bot.resolve_username_to_id(arg)
    if user_id:
        logging.info(f"Username '{arg}' успешно найден через Telethon, ID: {user_id}")
        return user_id

    # 2. Запасной метод: поиск в нашей базе данных
    logging.warning(f"Не удалось найти '{arg}' через Telethon, ищем в локальной БД...")
    user = await db.get_user_by_username(arg)
    if user:
        logging.info(f"Username '{arg}' найден в локальной БД, ID: {user['user_id']}")
        return user['user_id']
    
    return None

# --- Обработчики команд (ПЕРЕПИСАНЫ) ---

@dp.message(Command("stopuser"), is_subscribed_filter)
async def command_stop_user(message: Message):
    try:
        arg = message.text.split()[1]
        user_id = await resolve_user(arg)
        if user_id:
            await db.set_user_active_status(user_id, is_active=False)
            await message.answer(f"✅ Пользователь {arg} ({user_id}) был деактивирован.")
        else:
            await message.answer(f"❌ Пользователь {arg} не найден в базе данных.")
    except IndexError:
        await message.answer("Ошибка! Используйте формат: /stopuser <user_id или @username>")

@dp.message(Command("startuser"), is_subscribed_filter)
async def command_start_user(message: Message):
    try:
        arg = message.text.split()[1]
        user_id = await resolve_user(arg)
        if user_id:
            await db.set_user_active_status(user_id, is_active=True)
            await message.answer(f"✅ Пользователь {arg} ({user_id}) был активирован.")
        else:
            await message.answer(f"❌ Пользователь {arg} не найден в базе данных.")
    except IndexError:
        await message.answer("Ошибка! Используйте формат: /startuser <user_id или @username>")

# НОВАЯ КОМАНДА
@dp.message(Command("clearhistory"), is_subscribed_filter)
async def command_clear_history(message: Message):
    try:
        arg = message.text.split()[1]
        user_id = await resolve_user(arg)
        if user_id:
            await db.delete_history_for_user(user_id)
            await message.answer(f"✅ История диалога для пользователя {arg} ({user_id}) была полностью очищена.")
        else:
            await message.answer(f"❌ Пользователь {arg} не найден в базе данных.")
    except IndexError:
        await message.answer("Ошибка! Используйте формат: /clearhistory <user_id или @username>")

@dp.message(Command("updateprompt"), is_subscribed_filter)
async def command_update_prompt(message: Message):
    # ИЗМЕНЕНИЕ: Вызываем с флагом принудительного обновления
    await core_utils.update_prompt_cache(force=True)
    await message.answer("✅ Кэш промпта и инструментов был принудительно обновлен.")


@dp.message(Command("setmodel"), is_subscribed_filter)
async def command_set_model(message: Message):
    try:
        # Пример команды: /setmodel gemini-3-flash-preview
        new_model = message.text.split()[1]
        core_config.set_default_model(new_model) # Вызываем нашу новую функцию
        await message.answer(f"✅ Модель ИИ по умолчанию была изменена на: `{new_model}`")
    except IndexError:
        await message.answer(
            "Ошибка! Используйте формат: /setmodel <имя_модели>\n"
            f"Текущая модель: `{core_config.settings.default_model}`"
        )

@dp.message(Command("setmode"), is_subscribed_filter)
async def command_set_mode(message: Message):
    try:
        mode_str = message.text.split()[1]
        success = core_config.set_operation_mode(mode_str)
        if success:
            await message.answer(f"✅ Режим работы бота изменен на: `{mode_str}`")
        else:
            await message.answer(
                "❌ Неверный режим! Доступные режимы: `all`, `existing`, `none`."
            )
    except IndexError:
        current_mode_value = core_config.get_operation_mode_value()
        await message.answer(
            "Используйте формат: /setmode <режим>\n"
            "Доступные режимы: `all`, `existing`, `none`.\n"
            f"Текущий режим: `{current_mode_value}`"
        )

async def start():
    """Запускает Admin Bot."""
    await set_bot_commands(bot)
    print("Административный бот запускается...")
    await dp.start_polling(bot)
    print("Административный бот остановлен.")
