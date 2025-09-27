# /wrappers/filters.py

from aiogram import Bot
from aiogram.filters import Filter
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

class IsSubscribedFilter(Filter):
    def __init__(self, channel_id: str):
        self.channel_id = int(channel_id)

    async def __call__(self, message: Message, bot: Bot) -> bool:
        try:
            member = await bot.get_chat_member(
                chat_id=self.channel_id,
                user_id=message.from_user.id
            )
            # Пользователь считается подписчиком, если он не 'left' и не 'kicked'
            if member.status not in ["left", "kicked"]:
                return True
            else:
                await message.answer("Доступ запрещен. Вы должны быть подписаны на административный канал.")
                return False
        except TelegramBadRequest:
            # Ошибка возникает, если бот не в канале или ID неверный
            await message.answer("Ошибка проверки подписки. Обратитесь к администратору.")
            return False