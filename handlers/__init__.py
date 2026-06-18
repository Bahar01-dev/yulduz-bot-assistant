"""Пакет хэндлеров aiogram + гейт владельца (OwnerMiddleware, is_owner)."""

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, CallbackQuery

from config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Сообщение для чужих пользователей (бот — для одного владельца, spec.md §11)
NOT_OWNER_MESSAGE = "Этот бот работает только для своего владельца."


def is_owner(user_id: int) -> bool:
    """True, если user_id совпадает с владельцем из конфигурации."""
    return user_id == config.OWNER_TELEGRAM_ID


class OwnerMiddleware(BaseMiddleware):
    """Пропускает апдейты только от владельца, чужим отвечает отказом."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        # Достаём пользователя из апдейта (сообщение или callback)
        user = data.get("event_from_user")
        user_id = user.id if user else None

        # Чужой пользователь — прекращаем обработку
        if user_id is None or not is_owner(user_id):
            logger.warning("Отклонён доступ для user_id=%s", user_id)
            if isinstance(event, Message):
                await event.answer(NOT_OWNER_MESSAGE)
            elif isinstance(event, CallbackQuery):
                await event.answer(NOT_OWNER_MESSAGE, show_alert=True)
            return None

        # Владелец — продолжаем обработку
        return await handler(event, data)
