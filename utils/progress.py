"""Прогресс-индикация долгой генерации (статус-сообщение → результат).

Зачем: Claude пишет пост/Reels/план 10–40 секунд. Нативный индикатор
«печатает…» в Telegram гаснет через ~5 секунд, из-за чего кажется, что бот
завис («непонятно, пишет он или нет»). Вместо него сразу шлём видимое
сообщение-статус («✍️ Пишу пост…»), а по готовности РЕДАКТИРУЕМ это же
сообщение в готовый результат (или в текст ошибки). Пользователь всё время
видит, что процесс идёт, а ответ появляется на месте статуса — без мельтешения.

Использование:
    progress = await Progress.begin(callback.message, "✍️ Пишу пост…")
    result = await generate(...)
    if not result.ok:
        await progress.fail(result.user_message)
        return
    await progress.finish(pretty_text, reply_markup=actions_keyboard())
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, Message

from utils.logger import get_logger

logger = get_logger(__name__)


class Progress:
    """Статус-сообщение, которое заменяется финальным результатом.

    target  — сообщение, в чат которого шлём (callback.message или сообщение
              пользователя); на нём вызываем .answer.
    status  — уже отправленное сообщение-статус, которое будем редактировать.
    """

    def __init__(self, target: Message, status: Message) -> None:
        self._target = target
        self._status = status

    @classmethod
    async def begin(cls, target: Message, text: str) -> "Progress":
        """Отправляет статус-сообщение и возвращает управляющий объект."""
        status = await target.answer(text)
        return cls(target, status)

    async def finish(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Заменяет статус готовым результатом (редактирует то же сообщение).

        Если отредактировать нельзя (сообщение удалено, слишком старое и т.п.) —
        шлём результат новым сообщением, чтобы ответ точно дошёл.
        """
        try:
            await self._status.edit_text(text, reply_markup=reply_markup)
        except Exception:
            logger.debug("Не удалось отредактировать статус, шлю новым", exc_info=True)
            await self._target.answer(text, reply_markup=reply_markup)

    async def fail(self, text: str) -> None:
        """Заменяет статус текстом ошибки (без кнопок)."""
        await self.finish(text)
