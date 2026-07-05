"""Кэш трендового брифа за день (таблица trend_brief, spec.md §5.2, §20).

V2.1 «Трендовый бриф по расписанию + факты (grounding) в постах»: утренняя
задача (и ручной /тренды) собирает бриф, а его результат кэшируется за текущий
день. Тап по теме в обед берёт этот кэш — повторный веб-поиск не нужен, а факты
в хуках и посте согласованы (grounding = raw_search).

Схема: одна строка на дату (brief_date PRIMARY KEY). «🔁 Обновить» = upsert
(перезапись строки за сегодня свежим поиском).
"""

from __future__ import annotations

import json
from datetime import datetime

from database.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)


def today_str(tz_name: str | None = None) -> str:
    """Возвращает сегодняшнюю дату 'YYYY-MM-DD' в заданном IANA-поясе.

    Пояс по умолчанию берётся из config.TREND_TZ. Если пояс не распознан
    (некорректная строка) — падаем на локальное время процесса, чтобы дата
    всегда возвращалась и кэш не ломался.
    """
    if tz_name is None:
        from config import config

        tz_name = config.TREND_TZ
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name))
    except Exception:  # некорректный пояс / нет данных tz — локальное время
        now = datetime.now()
    return now.strftime("%Y-%m-%d")


async def upsert(brief_date: str, raw_search: str, topics: list[str]) -> None:
    """Создаёт или перезаписывает бриф за дату (topics → JSON).

    raw_search — сырые результаты Tavily (grounding для постов). topics — список
    тем брифа (текст), сохраняется как JSON для восстановления по индексу кнопки.
    """
    topics_json = json.dumps(topics or [], ensure_ascii=False)
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO trend_brief (brief_date, raw_search, topics_json)
            VALUES (?, ?, ?)
            ON CONFLICT(brief_date) DO UPDATE SET
                raw_search = excluded.raw_search,
                topics_json = excluded.topics_json,
                created_at = CURRENT_TIMESTAMP
            """,
            (brief_date, raw_search, topics_json),
        )
        await db.commit()
    logger.info("Сохранён трендовый бриф за %s (тем: %d)", brief_date, len(topics or []))


async def get(brief_date: str) -> dict | None:
    """Возвращает бриф за дату как dict или None, если строки нет.

    Ключи: brief_date, raw_search, topics (list[str]), created_at. topics_json
    разбирается обратно в список; при повреждённом JSON → пустой список.
    """
    async with get_connection() as db:
        async with db.execute(
            "SELECT * FROM trend_brief WHERE brief_date = ?",
            (brief_date,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["topics"] = json.loads(item.get("topics_json") or "[]")
    except (ValueError, TypeError):
        item["topics"] = []
    return item
