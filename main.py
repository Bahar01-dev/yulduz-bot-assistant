"""Точка входа бота: создаёт Bot и Dispatcher (aiogram 3.x), вешает гейт владельца,
регистрирует роутеры и запускает long polling.
"""

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from config import config
from database.db import init_db
from handlers import OwnerMiddleware
from handlers import callbacks as callbacks_handlers
from handlers import menu as menu_handlers
from handlers import message as message_handlers
from handlers import plan as plan_handlers
from handlers import start as start_handlers
from handlers import trends as trends_handlers
from handlers import voice as voice_handlers
from utils.errors import ERROR_CATALOG, Situation, alert_owner
from utils.logger import setup_logging, get_logger, log_exception


async def main() -> None:
    """Инициализирует и запускает бота."""
    # Логирование настраиваем до всего остального
    setup_logging()
    logger = get_logger(__name__)
    logger.info("Запуск бота...")

    # Инициализация БД до старта polling: создаём таблицы (idempotent)
    await init_db()

    # Bot с Markdown по умолчанию (aiogram 3.7 синтаксис)
    bot = Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()

    # Гейт владельца: middleware на сообщения и callback-и
    owner_mw = OwnerMiddleware()
    dp.message.middleware(owner_mw)
    dp.callback_query.middleware(owner_mw)

    # Глобальный error-handler (catch-all): ловит ЛЮБУЮ необработанную ошибку
    # в хэндлерах. Пользователю — RU-сообщение catch-all, разработчику — лог с
    # traceback, владельцу — алерт. Любой сбой не роняет бота.
    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        """Catch-all обработчик необработанных ошибок aiogram."""
        policy = ERROR_CATALOG[Situation.UNKNOWN]
        # Полный traceback в stdout (Railway logs) на уровне из каталога
        log_exception(
            logger,
            f"Необработанная ошибка: {type(event.exception).__name__}: {event.exception}",
            level=policy.log_level,
        )

        # Пытаемся показать пользователю дружелюбное RU-сообщение
        update = event.update
        try:
            if update.message is not None:
                await update.message.answer(policy.user_message)
            elif update.callback_query is not None:
                # Отвечаем на callback + сообщением в чат, если возможно
                await update.callback_query.answer(policy.user_message, show_alert=True)
        except Exception:  # отправка не должна ронять обработчик ошибок
            log_exception(logger, "Не удалось доставить RU-сообщение пользователю.")

        # Короткий алерт владельцу (троттлится внутри alert_owner)
        if policy.alert_owner:
            await alert_owner(bot, "Необработанная ошибка в боте. Подробности в логах.")

        # True — ошибка считается обработанной, бот продолжает работу
        return True

    # Регистрируем роутеры. ВАЖЕН порядок: start.router (CommandStart + FSM
    # онбординга) идёт ПЕРВЫМ, чтобы /start и состояния Onboarding.* ловились
    # раньше. message.router (свободный текст, StateFilter(None)) — ПОСЛЕ него.
    dp.include_router(start_handlers.router)
    dp.include_router(message_handlers.router)
    # callbacks.router (Фазы 6/7: post_hook:*/post:*, reel_hook:*/reel:*, menu,
    # menu:post, menu:reel) — ПОСЛЕ start и message.
    dp.include_router(callbacks_handlers.router)
    # menu.router (Фаза 8: /menu, menu:drafts, menu:profile, draft:open:*,
    # profile:*) — ПОСЛЕ остальных. Его пункты не пересекаются с фильтрами выше.
    dp.include_router(menu_handlers.router)
    # V2-роутеры: plan (/план, menu:plan, plan:*) и trends (/тренды, menu:trend,
    # trend:*). Колбэки уникальны, конфликтов с фильтрами выше нет.
    dp.include_router(plan_handlers.router)
    dp.include_router(trends_handlers.router)

    # voice.router (Фаза 9: F.voice → Groq Whisper → поток поста/Reels).
    # Регистрируем только при включённом голосовом вводе (есть GROQ_API_KEY).
    if config.VOICE_ENABLED:
        dp.include_router(voice_handlers.router)
        logger.info("Голосовой ввод включён (GROQ_API_KEY задан).")
    else:
        logger.info("Голосовой ввод выключен (GROQ_API_KEY не задан).")

    # Сбрасываем накопленные апдейты и стартуем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен. Ожидаю сообщения...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
