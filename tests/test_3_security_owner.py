"""ФУНКЦИЯ 3 — Безопасность single-user (handlers.OwnerMiddleware / is_owner).

Единственная защита бота: авторизации нет, бот открыт в Telegram, чужие user_id
не должны получать ответы. Проверяем предикат is_owner и гейт middleware:
владелец → хэндлер вызывается; чужой → хэндлер НЕ вызывается, обработка обрывается.
"""

import pytest

from handlers import is_owner, OwnerMiddleware


class _User:
    """Минимальный дублёр aiogram-пользователя (нужен только .id)."""

    def __init__(self, uid):
        self.id = uid


class _Event:
    """Нейтральный event (не Message/CallbackQuery) — чтобы проверить именно гейт,
    не задевая ветки отправки ответа."""


def test_is_owner_true_for_owner(owner_id):
    assert is_owner(owner_id) is True


def test_is_owner_false_for_stranger(owner_id):
    assert is_owner(owner_id + 1) is False
    assert is_owner(0) is False


@pytest.mark.asyncio
async def test_middleware_passes_owner(owner_id):
    """Владелец проходит гейт: вызываемый хэндлер получает управление."""
    called = {"hit": False}

    async def handler(event, data):
        called["hit"] = True
        return "OK"

    mw = OwnerMiddleware()
    data = {"event_from_user": _User(owner_id)}
    result = await mw(handler, _Event(), data)

    assert called["hit"] is True
    assert result == "OK"


@pytest.mark.asyncio
async def test_middleware_blocks_stranger(owner_id):
    """Чужой user_id: хэндлер НЕ вызывается, обработка возвращает None."""
    called = {"hit": False}

    async def handler(event, data):
        called["hit"] = True
        return "OK"

    mw = OwnerMiddleware()
    data = {"event_from_user": _User(owner_id + 12345)}
    result = await mw(handler, _Event(), data)

    assert called["hit"] is False
    assert result is None


@pytest.mark.asyncio
async def test_middleware_blocks_missing_user(owner_id):
    """Апдейт без пользователя — тоже блокируется (fail-closed)."""
    called = {"hit": False}

    async def handler(event, data):
        called["hit"] = True
        return "OK"

    mw = OwnerMiddleware()
    result = await mw(handler, _Event(), {"event_from_user": None})

    assert called["hit"] is False
    assert result is None
