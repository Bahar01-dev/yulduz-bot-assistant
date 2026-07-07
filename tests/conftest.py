"""Общая настройка тестов.

ВАЖНО: DATABASE_PATH подменяется на временный файл ДО первого импорта config,
чтобы тесты БД не трогали рабочую data/bot.db. load_dotenv(override=False) не
перетирает уже выставленную переменную окружения — поэтому temp-путь выигрывает.
"""

import os
import tempfile

# Изолированная БД для тестов — выставляем ДО импорта config кем-либо.
_TMP_DB = os.path.join(tempfile.gettempdir(), "bot_test.db")
os.environ["DATABASE_PATH"] = _TMP_DB

# Гарантируем обязательные ключи, если тесты запускают без .env.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "506813942")

import asyncio

import pytest

from database.db import init_db, get_connection


@pytest.fixture(scope="session")
def owner_id() -> int:
    from config import config
    return config.OWNER_TELEGRAM_ID


@pytest.fixture()
async def clean_db():
    """Чистая БД: пересоздаёт файл-схему и вычищает таблицы перед каждым тестом."""
    # Удаляем старый файл, создаём схему заново.
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    await init_db()
    async with get_connection() as db:
        for table in ("brand_profile", "publications", "drafts", "style_feedback"):
            await db.execute(f"DELETE FROM {table}")
        await db.commit()
    yield
