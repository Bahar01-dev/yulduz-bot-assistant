"""ФУНКЦИЯ 1 — Онбординг и сохранение профиля бренда (database/brand_profile.py).

Фундамент бота: без профиля весь контент пишется «не в стиле». Проверяем полный
CRUD, который наполняет онбординг: create → exists → get_by_user → update, а также
защиту update() от посторонних полей (whitelist _UPDATABLE_FIELDS).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import handlers.start as start_mod
from database import brand_profile
from database.db import get_connection

pytestmark = pytest.mark.asyncio


async def test_profile_absent_before_onboarding(clean_db, owner_id):
    """До онбординга профиля нет: exists=False, get_by_user=None."""
    assert await brand_profile.exists(owner_id) is False
    assert await brand_profile.get_by_user(owner_id) is None


async def test_create_and_read_profile(clean_db, owner_id):
    """5 шагов онбординга → профиль сохранён и читается по user_id."""
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="уход за кожей",
        target_audience="женщины 25-40",
        tone="тёплый, экспертный",
        forbidden_words="дешёвый, химия",
        reference_accounts="@skincare_guru",
        style_description="разговорный, с примерами",
    )
    assert await brand_profile.exists(owner_id) is True

    profile = await brand_profile.get_by_user(owner_id)
    assert profile is not None
    assert profile["niche"] == "уход за кожей"
    assert profile["target_audience"] == "женщины 25-40"
    assert profile["tone"] == "тёплый, экспертный"
    assert profile["forbidden_words"] == "дешёвый, химия"


async def test_optional_fields_default_none(clean_db, owner_id):
    """Необязательные поля можно не передавать — сохраняются как NULL."""
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="фитнес",
        target_audience="мужчины 20-35",
        tone="дерзкий",
    )
    profile = await brand_profile.get_by_user(owner_id)
    assert profile["forbidden_words"] is None
    assert profile["reference_accounts"] is None
    assert profile["style_description"] is None


async def test_update_changes_field(clean_db, owner_id):
    """update() меняет разрешённое поле профиля."""
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="фитнес",
        target_audience="мужчины 20-35",
        tone="дерзкий",
    )
    await brand_profile.update(owner_id, tone="спокойный, вдумчивый")
    profile = await brand_profile.get_by_user(owner_id)
    assert profile["tone"] == "спокойный, вдумчивый"


async def test_update_ignores_unknown_fields(clean_db, owner_id):
    """update() принимает только whitelist-поля — «инъекция» лишнего игнорируется."""
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="фитнес",
        target_audience="мужчины 20-35",
        tone="дерзкий",
    )
    # Произвольные (не whitelist) поля не должны попасть в UPDATE, а
    # разрешённое поле — обновиться. (user_telegram_id — позиционный параметр
    # функции, его нельзя передать в **fields, поэтому проверяем чужое поле.)
    await brand_profile.update(owner_id, hacker="x", is_admin=True, niche="бег")
    profile = await brand_profile.get_by_user(owner_id)
    assert profile["user_telegram_id"] == owner_id  # владелец не перезаписан
    assert profile["niche"] == "бег"                 # разрешённое поле обновилось
    assert "hacker" not in profile.keys()            # мусорное поле не создано


# ─────────── Изменение A: create() идемпотентен (устраняет IntegrityError) ───────────


async def test_create_is_idempotent_upsert(clean_db, owner_id):
    """Повторный create для того же user_id не падает (UNIQUE) и перезаписывает поля.

    Раньше голый INSERT бросал IntegrityError на дубликате user_telegram_id.
    Теперь ON CONFLICT DO UPDATE → профиль обновляется, запись остаётся одна.
    """
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="фитнес",
        target_audience="мужчины 20-35",
        tone="дерзкий",
    )
    # Второй вызов с тем же ключом — не должен падать.
    await brand_profile.create(
        user_telegram_id=owner_id,
        niche="бег",
        target_audience="мужчины 30-45",
        tone="спокойный",
        forbidden_words="лёгкий",
    )

    profile = await brand_profile.get_by_user(owner_id)
    assert profile["niche"] == "бег"                    # поля перезаписаны
    assert profile["tone"] == "спокойный"
    assert profile["forbidden_words"] == "лёгкий"

    # Профиль по-прежнему один (upsert, а не второй INSERT).
    async with get_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) AS c FROM brand_profile WHERE user_telegram_id = ?",
            (owner_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row["c"] == 1


# ─────────── Изменение B: сбой БД в on_refs обрабатывается корректно ───────────


async def test_onboarding_save_db_failure_is_handled(owner_id, monkeypatch):
    """Сбой БД при сохранении профиля: RU-сообщение из каталога, алерт владельцу, FSM НЕ очищен.

    При ошибке записи пользователь не должен видеть техн.текст, владелец —
    получить алерт, а состояние онбординга — сохраниться (повторная отправка
    референсов ретраит сохранение, а не переигрывает 5 шагов).
    """

    async def _boom(**kwargs):
        raise RuntimeError("disk full")

    alert = AsyncMock()
    monkeypatch.setattr(start_mod.brand_profile, "create", _boom)
    monkeypatch.setattr(start_mod, "alert_owner", alert)

    message = MagicMock()
    message.from_user.id = owner_id
    message.text = "@ref_account"
    message.answer = AsyncMock()
    message.bot = MagicMock()

    state = MagicMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(
        return_value={
            "niche": "фитнес",
            "audience": "мужчины 20-35",
            "tones": ["Дружеский"],
            "forbidden": "",
            "refs": "@ref_account",
        }
    )
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    await start_mod.on_refs(message, state)

    # Пользователю — ровно текст DB_ERROR из каталога (без техн.деталей).
    answered = [c.args[0] for c in message.answer.await_args_list]
    assert start_mod._DB_ERROR_TEXT in answered
    assert "disk full" not in " ".join(answered)
    # Владельцу — алерт.
    alert.assert_awaited()
    # FSM НЕ очищен: онбординг можно доиграть повторной отправкой.
    state.clear.assert_not_awaited()
