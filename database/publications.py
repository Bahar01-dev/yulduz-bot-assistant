"""CRUD истории публикаций (таблица publications, spec.md §5.2).

Долгосрочная память: что уже писали и на какие темы.
get_recent_topics() — для дедупликации тем в промпте (spec.md §13.2).
"""

import json

from database.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)


async def save(
    user_telegram_id: int,
    content_type: str,
    topic: str,
    content: str,
    hooks: list | None = None,
    selected_hook: int | None = None,
) -> None:
    """Сохраняет публикацию. hooks (список) кладётся как JSON-строка."""
    hooks_json = json.dumps(hooks, ensure_ascii=False) if hooks is not None else None
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO publications (
                user_telegram_id, content_type, topic, content, hooks, selected_hook
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_telegram_id, content_type, topic, content, hooks_json, selected_hook),
        )
        await db.commit()
    logger.info(
        "Сохранена публикация user=%s, type=%s, topic=%r",
        user_telegram_id,
        content_type,
        topic,
    )


async def get_recent_topics(user_telegram_id: int, limit: int = 20) -> list[str]:
    """Возвращает последние темы по убыванию saved_at (для дедупликации)."""
    async with get_connection() as db:
        async with db.execute(
            """
            SELECT topic FROM publications
            WHERE user_telegram_id = ?
            ORDER BY saved_at DESC
            LIMIT ?
            """,
            (user_telegram_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [row["topic"] for row in rows]


async def list_recent(user_telegram_id: int, limit: int = 10) -> list[dict]:
    """Возвращает последние публикации целиком (для истории). hooks разбирается из JSON."""
    async with get_connection() as db:
        async with db.execute(
            """
            SELECT * FROM publications
            WHERE user_telegram_id = ?
            ORDER BY saved_at DESC
            LIMIT ?
            """,
            (user_telegram_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

    result = []
    for row in rows:
        item = dict(row)
        # Разбираем JSON хуков обратно в список (если есть)
        if item.get("hooks"):
            item["hooks"] = json.loads(item["hooks"])
        result.append(item)
    return result
