"""CRUD черновиков (таблица drafts, spec.md §5.2).

Долгосрочная память: созданный, но ещё не опубликованный контент.
status: 'draft' | 'approved'.
"""

import json

from database.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)


async def save(
    user_telegram_id: int,
    content_type: str,
    topic: str | None,
    content: str,
    hooks: list | None = None,
    status: str = "draft",
) -> int:
    """Сохраняет черновик и возвращает id новой записи. hooks → JSON-строка."""
    hooks_json = json.dumps(hooks, ensure_ascii=False) if hooks is not None else None
    async with get_connection() as db:
        cursor = await db.execute(
            """
            INSERT INTO drafts (
                user_telegram_id, content_type, topic, content, hooks, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_telegram_id, content_type, topic, content, hooks_json, status),
        )
        await db.commit()
        draft_id = cursor.lastrowid
    logger.info(
        "Сохранён черновик id=%s user=%s type=%s", draft_id, user_telegram_id, content_type
    )
    return draft_id


async def list_by_user(user_telegram_id: int) -> list[dict]:
    """Возвращает все черновики пользователя (свежие сверху). hooks разбирается из JSON."""
    async with get_connection() as db:
        async with db.execute(
            """
            SELECT * FROM drafts
            WHERE user_telegram_id = ?
            ORDER BY created_at DESC
            """,
            (user_telegram_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    result = []
    for row in rows:
        item = dict(row)
        if item.get("hooks"):
            item["hooks"] = json.loads(item["hooks"])
        result.append(item)
    return result


async def get(draft_id: int) -> dict | None:
    """Возвращает черновик по id как dict или None. hooks разбирается из JSON."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT * FROM drafts WHERE id = ?",
            (draft_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return None
    item = dict(row)
    if item.get("hooks"):
        item["hooks"] = json.loads(item["hooks"])
    return item


async def set_status(draft_id: int, status: str) -> None:
    """Меняет статус черновика ('draft' | 'approved')."""
    async with get_connection() as db:
        await db.execute(
            "UPDATE drafts SET status = ? WHERE id = ?",
            (status, draft_id),
        )
        await db.commit()
    logger.info("Черновик id=%s → статус %r", draft_id, status)
