"""CRUD обратной связи по стилю (таблица style_feedback, spec.md §5.2, §13.3).

Система обучения стилю (V2): под готовым постом/сценарием пользователь жмёт
[👍 Одобрить] / [👎 Отклонить]. Фрагмент контента сохраняется с вердиктом
'approved' | 'rejected'. Накопленные примеры подмешиваются в системный промпт
(«Примеры ХОРОШЕГО стиля… / Примеры ПЛОХОГО стиля…»), чтобы бот точнее попадал
в голос владельца — см. prompts/style_builder.build_system_prompt().
"""

from database.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

# Допустимые вердикты (валидация на входе save()).
APPROVED = "approved"
REJECTED = "rejected"
_VALID_VERDICTS = (APPROVED, REJECTED)

# Максимальная длина сохраняемого фрагмента — чтобы примеры в промпте оставались
# компактными (целый длинный пост раздувал бы system-промпт).
_MAX_SNIPPET = 600


async def save(user_telegram_id: int, content_snippet: str, verdict: str) -> int:
    """Сохраняет фрагмент контента с вердиктом и возвращает id новой записи.

    verdict должен быть 'approved' или 'rejected' (иначе ValueError). Фрагмент
    обрезается до _MAX_SNIPPET символов.
    """
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"Недопустимый вердикт: {verdict!r}")

    snippet = (content_snippet or "").strip()
    if len(snippet) > _MAX_SNIPPET:
        snippet = snippet[:_MAX_SNIPPET].rstrip() + "…"

    async with get_connection() as db:
        cursor = await db.execute(
            """
            INSERT INTO style_feedback (
                user_telegram_id, content_snippet, verdict
            ) VALUES (?, ?, ?)
            """,
            (user_telegram_id, snippet, verdict),
        )
        await db.commit()
        feedback_id = cursor.lastrowid
    logger.info(
        "Сохранён style_feedback id=%s user=%s verdict=%s",
        feedback_id,
        user_telegram_id,
        verdict,
    )
    return feedback_id


async def _recent_by_verdict(user_telegram_id: int, verdict: str, limit: int) -> list[str]:
    """Возвращает последние фрагменты пользователя с заданным вердиктом."""
    async with get_connection() as db:
        async with db.execute(
            """
            SELECT content_snippet FROM style_feedback
            WHERE user_telegram_id = ? AND verdict = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_telegram_id, verdict, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [row["content_snippet"] for row in rows if row["content_snippet"]]


async def get_examples(
    user_telegram_id: int,
    approved_limit: int = 3,
    rejected_limit: int = 3,
) -> dict[str, list[str]]:
    """Собирает примеры стиля для системного промпта (§13.3).

    Возвращает dict {"approved": [...], "rejected": [...]} с самыми свежими
    фрагментами каждого вердикта. Списки могут быть пустыми (фидбэка ещё нет) —
    тогда build_system_prompt просто не добавит секцию примеров.
    """
    approved = await _recent_by_verdict(user_telegram_id, APPROVED, approved_limit)
    rejected = await _recent_by_verdict(user_telegram_id, REJECTED, rejected_limit)
    return {"approved": approved, "rejected": rejected}
