"""Планировщик утреннего трендового брифа (V2.1, spec.md §20).

APScheduler (AsyncIOScheduler) внутри процесса бота: бот и так крутится 24/7 на
long-polling, отдельный cron/сервис не нужен. Одна ежедневная cron-задача в
TREND_DIGEST_TIME (пояс TREND_TZ) собирает бриф и присылает его владельцу с
кнопками тем — тот же бриф, что и ручной /тренды.

Устойчивость (§20.6):
  - стартует ТОЛЬКО при TRENDS_ENABLED (есть TAVILY_API_KEY); иначе no-op;
  - задача целиком в try/except — сбой сборки не роняет планировщик;
  - при рестарте расписание регистрируется заново из env, кэш брифа — в БД.
"""

from __future__ import annotations

from typing import Any

from agents import trend_analyst
from config import config
from database import brand_profile, trend_brief
from utils.formatter import format_brief
from utils.keyboards import brief_keyboard
from utils.logger import get_logger

logger = get_logger(__name__)

# Сообщения владельцу для утренней доставки.
_NO_PROFILE = (
    "Доброе утро! Хотел прислать тренды дня, но профиль ещё не настроен. "
    "Нажми /start, и завтра пришлю бриф 🙌"
)
_FAILED = "Сегодня тренды подтянуть не удалось 🙈 Попробуй собрать вручную: /тренды"


def _parse_time(value: str) -> tuple[int, int]:
    """Разбирает 'HH:MM' в (hour, minute); при ошибке — дефолт 09:00."""
    try:
        hour_str, minute_str = value.strip().split(":", 1)
        hour, minute = int(hour_str), int(minute_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    logger.warning("Некорректный TREND_DIGEST_TIME=%r → дефолт 09:00", value)
    return 9, 0


async def _run_daily_brief(bot: Any) -> None:
    """Ежедневная задача: собрать бриф и прислать владельцу (§20.3).

    Целиком в try/except: любая ошибка логируется, но не всплывает в планировщик.
    """
    owner_id = config.OWNER_TELEGRAM_ID
    logger.info("Утренний трендовый бриф: старт (owner=%s)", owner_id)
    try:
        profile = await brand_profile.get_by_user(owner_id)
        if not profile:
            await bot.send_message(chat_id=owner_id, text=_NO_PROFILE)
            return

        result = await trend_analyst.generate_brief(profile)
        if not result.ok:
            logger.warning("Утренний бриф не собран [%s]", result.situation)
            await bot.send_message(chat_id=owner_id, text=_FAILED)
            return

        pretty = format_brief(result.brief_text)
        if not pretty:
            await bot.send_message(chat_id=owner_id, text=_FAILED)
            return

        brief_date = trend_brief.today_str()
        await trend_brief.upsert(brief_date, result.raw_search or "", result.topics)

        await bot.send_message(
            chat_id=owner_id,
            text=pretty,
            reply_markup=brief_keyboard(brief_date, result.topics),
        )
        logger.info("Утренний бриф отправлен владельцу (тем: %d)", len(result.topics))
    except Exception:
        logger.exception("Сбой утренней задачи трендового брифа")


def start_scheduler(bot: Any):
    """Создаёт и запускает планировщик утреннего брифа. Возвращает scheduler|None.

    Стартует только при TRENDS_ENABLED. Вызывать из main() внутри работающего
    event loop. Ссылку на scheduler держит вызывающий код (иначе GC).
    """
    if not config.TRENDS_ENABLED:
        logger.info("Планировщик трендов выключен (нет TAVILY_API_KEY).")
        return None

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("Пакет 'apscheduler' не установлен — планировщик не запущен.")
        return None

    hour, minute = _parse_time(config.TREND_DIGEST_TIME)
    scheduler = AsyncIOScheduler(timezone=config.TREND_TZ)
    scheduler.add_job(
        _run_daily_brief,
        trigger="cron",
        hour=hour,
        minute=minute,
        args=[bot],
        id="daily_trend_brief",
        replace_existing=True,
        misfire_grace_time=3600,  # переживаем короткие простои процесса
    )
    scheduler.start()
    logger.info(
        "Планировщик трендов запущен: ежедневно в %02d:%02d (%s).",
        hour, minute, config.TREND_TZ,
    )
    return scheduler
