"""Инициализация SQLite и соединение (aiosqlite).

init_db() создаёт 4 таблицы (spec.md §5.2) при старте бота.
get_connection() — async-контекстный менеджер для получения соединения
с row_factory = aiosqlite.Row (чтение колонок по именам).
"""

import os
from contextlib import asynccontextmanager

import aiosqlite

from config import config
from utils.logger import get_logger

logger = get_logger(__name__)

# SQL-схема (дословно по spec.md §5.2, с IF NOT EXISTS)

_CREATE_BRAND_PROFILE = """
CREATE TABLE IF NOT EXISTS brand_profile (
    id INTEGER PRIMARY KEY,
    user_telegram_id INTEGER UNIQUE NOT NULL,
    niche TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    tone TEXT NOT NULL,
    forbidden_words TEXT,
    reference_accounts TEXT,
    style_description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_PUBLICATIONS = """
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    user_telegram_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    hooks TEXT,
    selected_hook INTEGER,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_DRAFTS = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    user_telegram_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    topic TEXT,
    content TEXT NOT NULL,
    hooks TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_STYLE_FEEDBACK = """
CREATE TABLE IF NOT EXISTS style_feedback (
    id INTEGER PRIMARY KEY,
    user_telegram_id INTEGER NOT NULL,
    content_snippet TEXT NOT NULL,
    verdict TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Кэш трендового брифа за день (V2.1, §5.2/§20): одна строка на дату.
_CREATE_TREND_BRIEF = """
CREATE TABLE IF NOT EXISTS trend_brief (
    brief_date TEXT PRIMARY KEY,
    raw_search TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_ALL_TABLES = (
    _CREATE_BRAND_PROFILE,
    _CREATE_PUBLICATIONS,
    _CREATE_DRAFTS,
    _CREATE_STYLE_FEEDBACK,
    _CREATE_TREND_BRIEF,
)


def _ensure_db_dir() -> None:
    """Создаёт папку под файл БД, если её ещё нет."""
    db_dir = os.path.dirname(config.DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


@asynccontextmanager
async def get_connection():
    """Async-контекстный менеджер: соединение с включённым row_factory.

    Использование:
        async with get_connection() as db:
            await db.execute(...)
            await db.commit()
    """
    conn = await aiosqlite.connect(config.DATABASE_PATH)
    conn.row_factory = aiosqlite.Row  # читаем колонки по именам
    try:
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    """Создаёт папку под БД и все 4 таблицы (idempotent: IF NOT EXISTS)."""
    _ensure_db_dir()
    async with get_connection() as db:
        for ddl in _ALL_TABLES:
            await db.execute(ddl)
        await db.commit()
    logger.info("База данных инициализирована: %s (5 таблиц)", config.DATABASE_PATH)
