# /wrappers/telegram_bot.py

from telethon import TelegramClient, events
import logging

logger = logging.getLogger(__name__)
import io
from core import config, api as core_api
from core import database as db
from core import config, api as core_api, database as core_db
from core.config import OperationMode
from telethon.tl.types import User
client = TelegramClient(
    config.settings.SESSION_NAME,
    int(config.settings.TELEGRAM_API_ID),
    config.settings.TELEGRAM_API_HASH
)

@client.on(events.NewMessage(incoming=True))
async def handle_new_message(event):
    logger.info(f"Получено сообщение! От: {event.sender_id}, Тип: {type(event.sender)}, Текст: {event.message.text}")
    
    if config.settings.operation_mode == OperationMode.NONE:
        logger.info("Режим NONE - игнорируем сообщение")
        return
    
    # Проверяем, что это личное сообщение
    if not event.is_private:
        logger.info("Сообщение не из личного чата - игнорируем")
        return
    
    # Проверяем, что это не исходящее сообщение
    if event.out:
        logger.info("Исходящее сообщение - игнорируем")
        return
    
    # Получаем информацию об отправителе
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username if sender else None
        is_bot = sender.bot if sender else False
    except Exception as e:
        logger.warning(f"Не удалось получить информацию об отправителе: {e}")
        username = None
        is_bot = False
    
    # Игнорируем ботов
    if is_bot:
        logger.info("Сообщение от бота - игнорируем")
        return
    await db.add_or_get_user(user_id, username) # Убедимся, что пользователь есть в БД
    text_content = None
    audio_content = None

    if event.message.text:
        text_content = event.message.text
        logger.info(f"Получено текстовое сообщение от user_id: {user_id}")
    elif event.message.voice or event.message.audio:
        logger.info(f"Получено аудио/голосовое сообщение от user_id: {user_id}")
        buffer = io.BytesIO()
        await event.message.download_media(file=buffer)
        audio_content = buffer.getvalue()
    else:
        return

    response_dict = None
    try:
        # ТВОЙ НАДЕЖНЫЙ МЕТОД + АДАПТАЦИЯ
        action_type = 'typing' if text_content else 'record-audio'
        try:
            # Сначала пытаемся выполнить с анимацией "печатает"
            async with client.action(await event.get_input_sender(), action_type):
                response_dict = await core_api.process_message(
                    user_id=user_id,
                    text=text_content,
                    audio_data=audio_content
                )
        except Exception as e:
            # Если анимация не удалась, просто вызываем ядро без нее
            logger.error(f"Ошибка при установке action '{action_type}': {e}")
            response_dict = await core_api.process_message(
                user_id=user_id,
                text=text_content,
                audio_data=audio_content
            )
        if response_dict is None:
            return
        # Обрабатываем ответ от ядра
        response_type = response_dict.get("type")
        response_content = response_dict.get("content")

        if response_type == "text":
            await event.respond(response_content)
        elif response_type == "tool_calls":
            tool_call = response_content[0]
            func_name = tool_call.function.name
            func_args = tool_call.function.arguments
            
            await event.respond(
                f"🤖 AI хочет выполнить действие:\n"
                f"**Функция:** `{func_name}`\n"
                f"**Аргументы:** `{func_args}`"
            )
        elif response_type == "error":
            await event.respond(f"Произошла ошибка: {response_content}")

    except Exception as e:
        logger.error(f"Критическая ошибка в обработчике сообщений: {e}")
        await event.respond("Извините, произошла непредвиденная ошибка.")

async def resolve_username_to_id(username: str) -> int | None:
    """
    Использует активный клиент Telethon для поиска user_id по username.
    Это "живой" поиск через API Telegram.
    """
    try:
        # Убираем возможный символ '@'
        clean_username = username.lstrip('@')
        # get_entity - мощная функция Telethon для поиска
        entity = await client.get_entity(clean_username)
        return entity.id
    except (ValueError, TypeError, AttributeError):
        # ValueError - если пользователь не найден
        # TypeError, AttributeError - на случай других неожиданных результатов
        logger.warning(f"Не удалось найти пользователя по username '{username}' через API Telethon.")
        return None



async def start():
    """Основная асинхронная функция для запуска клиента."""
    print("Бот запускается...")
    await client.start()
    
    # Проверяем авторизацию
    me = await client.get_me()
    print(f"Авторизован как: {me.first_name} (@{me.username})")
    print(f"ID: {me.id}")
    
    print("Бот успешно запущен и слушает сообщения.")
    await client.run_until_disconnected()