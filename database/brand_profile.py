"""CRUD профиля бренда (таблица brand_profile, spec.md §5.2).

Постоянная память: ниша, аудитория, тон, запреты, референсы, описание стиля.
Профиль один на пользователя (user_telegram_id UNIQUE).
"""

from database.db import get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

# Поля, которые разрешено обновлять через update()
_UPDATABLE_FIELDS = (
    "niche",
    "target_audience",
    "tone",
    "forbidden_words",
    "reference_accounts",
    "style_description",
)


async def create(
    user_telegram_id: int,
    niche: str,
    target_audience: str,
    tone: str,
    forbidden_words: str | None = None,
    reference_accounts: str | None = None,
    style_description: str | None = None,
) -> None:
    """Создаёт профиль бренда для пользователя (идемпотентно).

    ON CONFLICT(user_telegram_id) DO UPDATE: повторный онбординг, гонка двух
    /start или ретрай после сбоя не падают с IntegrityError, а перезаписывают
    профиль (created_at сохраняется, updated_at обновляется). Тот же паттерн
    upsert, что и в database/trend_brief.upsert().
    """
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO brand_profile (
                user_telegram_id, niche, target_audience, tone,
                forbidden_words, reference_accounts, style_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_telegram_id) DO UPDATE SET
                niche = excluded.niche,
                target_audience = excluded.target_audience,
                tone = excluded.tone,
                forbidden_words = excluded.forbidden_words,
                reference_accounts = excluded.reference_accounts,
                style_description = excluded.style_description,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_telegram_id,
                niche,
                target_audience,
                tone,
                forbidden_words,
                reference_accounts,
                style_description,
            ),
        )
        await db.commit()
    logger.info("Сохранён профиль бренда для user_telegram_id=%s", user_telegram_id)


async def get_by_user(user_telegram_id: int) -> dict | None:
    """Возвращает профиль как dict или None, если его нет."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT * FROM brand_profile WHERE user_telegram_id = ?",
            (user_telegram_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def update(user_telegram_id: int, **fields) -> None:
    """Обновляет переданные поля профиля + updated_at = CURRENT_TIMESTAMP.

    Принимает только поля из _UPDATABLE_FIELDS; остальные игнорируются.
    """
    # Оставляем только допустимые поля
    valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
    if not valid:
        logger.warning("update(): нет полей для обновления (user=%s)", user_telegram_id)
        return

    # Собираем SET-часть из имён колонок (имена — не пользовательский ввод)
    set_clause = ", ".join(f"{name} = ?" for name in valid)
    set_clause += ", updated_at = CURRENT_TIMESTAMP"
    params = list(valid.values()) + [user_telegram_id]

    async with get_connection() as db:
        await db.execute(
            f"UPDATE brand_profile SET {set_clause} WHERE user_telegram_id = ?",
            params,
        )
        await db.commit()
    logger.info(
        "Обновлён профиль бренда user=%s, поля: %s",
        user_telegram_id,
        list(valid.keys()),
    )


async def exists(user_telegram_id: int) -> bool:
    """True, если профиль для пользователя уже существует."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT 1 FROM brand_profile WHERE user_telegram_id = ? LIMIT 1",
            (user_telegram_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return row is not None
