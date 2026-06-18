"""Меню, черновики и профиль (Фаза 8, spec.md §10).

Содержит:
  - команду /menu (показ главного меню, если профиль есть);
  - просмотр черновиков: menu:drafts → список → draft:open:<id> → карточка;
  - просмотр профиля: menu:profile → карточка + «Изменить»;
  - редактирование профиля по полям: profile:edit → выбор поля →
    текстовый ввод (ниша/аудитория/запреты/референсы) или мультивыбор тона.

Навигация без тупиков: из любой карточки есть «🏠 В меню» (обрабатывается в
handlers/callbacks.py, сбрасывает FSM), из списка/редактирования — возврат назад.

Роутер регистрируется в main.py ПОСЛЕ start/message/callbacks. Пункты
menu:drafts/menu:profile сюда доходят без конфликта (start.py их больше не ловит,
callbacks.py ловит только menu:post/menu:reel/menu).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from agents import onboarding
from database import brand_profile, drafts
from utils.errors import alert_owner
from utils.formatter import _escape_md, format_post, format_reel
from utils.keyboards import (
    draft_view_keyboard,
    drafts_list_keyboard,
    main_menu_keyboard,
    profile_fields_keyboard,
    profile_view_keyboard,
    tone_keyboard,
)
from utils.logger import get_logger
from utils.state import ProfileEdit

logger = get_logger(__name__)

router = Router(name="menu")

MENU_PROMPT = "Чем займёмся?"
NO_PROFILE_TEXT = "Нужно сначала настроить профиль. Нажми /start"
NO_DRAFTS_TEXT = "Черновиков пока нет. Создай пост или Reels и нажми 💾 Сохранить."
DRAFT_NOT_FOUND = "Черновик не найден."

# Ключ поля → колонка БД (для текстовых полей; тон обрабатывается отдельно).
_FIELD_COLUMN = {
    "niche": "niche",
    "audience": "target_audience",
    "forbidden": "forbidden_words",
    "refs": "reference_accounts",
}

# Ключ поля → приглашение ввести новое значение.
_FIELD_PROMPT = {
    "niche": "Введи новую нишу:",
    "audience": "Опиши аудиторию (возраст, профессия, главная боль):",
    "forbidden": "Какие слова/фразы под запретом? Перечисли через запятую (или «-», чтобы очистить).",
    "refs": "Назови 1-3 референс-аккаунта (@ или ссылка):",
}

# Ключ поля → человекочитаемое название (для подтверждения).
_FIELD_TITLE = {
    "niche": "Ниша",
    "audience": "Аудитория",
    "tone": "Тон",
    "forbidden": "Запрещённые слова",
    "refs": "Референсы",
}

# Поля, которые нельзя очищать (NOT NULL в схеме §5.2).
_REQUIRED_FIELDS = {"niche", "audience"}


# ───────────────────────── Хелперы ─────────────────────────


def _render_profile(profile: dict) -> str:
    """Собирает карточку профиля в Telegram-Markdown (значения экранируются)."""

    def val(key: str, empty: str = "—") -> str:
        raw = (profile.get(key) or "").strip()
        return _escape_md(raw) if raw else empty

    return (
        "⚙️ *Твой профиль*\n\n"
        f"*Ниша:* {val('niche')}\n"
        f"*Аудитория:* {val('target_audience')}\n"
        f"*Тон:* {val('tone')}\n"
        f"*Запрещённые слова:* {val('forbidden_words')}\n"
        f"*Референсы:* {val('reference_accounts')}"
    )


async def _show_profile(message: Message, user_id: int) -> None:
    """Загружает профиль и показывает его карточку с кнопками действий."""
    profile = await brand_profile.get_by_user(user_id)
    if not profile:
        await message.answer(NO_PROFILE_TEXT)
        return
    await message.answer(_render_profile(profile), reply_markup=profile_view_keyboard())


# ───────────────────────── /menu ─────────────────────────


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    """Команда /menu: показывает главное меню (если профиль настроен)."""
    await state.clear()
    if not await brand_profile.exists(message.from_user.id):
        await message.answer(NO_PROFILE_TEXT)
        return
    await message.answer(MENU_PROMPT, reply_markup=main_menu_keyboard())


# ───────────────────────── Черновики ─────────────────────────


@router.callback_query(F.data == "menu:drafts")
async def on_menu_drafts(callback: CallbackQuery, state: FSMContext) -> None:
    """💾 Черновики: показать список сохранённого контента."""
    await callback.answer()
    await state.clear()
    items = await drafts.list_by_user(callback.from_user.id)
    if not items:
        await callback.message.answer(NO_DRAFTS_TEXT, reply_markup=main_menu_keyboard())
        return
    await callback.message.answer(
        f"💾 *Твои черновики* ({len(items)}):",
        reply_markup=drafts_list_keyboard(items[:20]),
    )


@router.callback_query(F.data.startswith("draft:open:"))
async def on_draft_open(callback: CallbackQuery) -> None:
    """Открыть черновик по id: показать отформатированный контент."""
    await callback.answer()
    try:
        draft_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer(DRAFT_NOT_FOUND)
        return

    item = await drafts.get(draft_id)
    # Проверяем и существование, и принадлежность владельцу (single-user, но строго).
    if item is None or item.get("user_telegram_id") != callback.from_user.id:
        await callback.message.answer(DRAFT_NOT_FOUND)
        return

    content = item.get("content", "")
    if item.get("content_type") == "reel":
        pretty = format_reel(content)
    else:
        pretty = format_post(content)

    topic = (item.get("topic") or "без темы").strip()
    text = f"Тема: {_escape_md(topic)}\n\n{pretty}"
    await callback.message.answer(text, reply_markup=draft_view_keyboard())


# ───────────────────────── Профиль: просмотр ─────────────────────────


@router.callback_query(F.data == "menu:profile")
async def on_menu_profile(callback: CallbackQuery, state: FSMContext) -> None:
    """⚙️ Профиль: показать карточку профиля."""
    await callback.answer()
    await state.clear()
    await _show_profile(callback.message, callback.from_user.id)


@router.callback_query(F.data == "profile:edit")
async def on_profile_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """✏️ Изменить профиль: показать выбор поля."""
    await callback.answer()
    await state.set_state(ProfileEdit.choosing_field)
    await callback.message.answer("Что изменить?", reply_markup=profile_fields_keyboard())


@router.callback_query(F.data.startswith("profile:field:"))
async def on_profile_field(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор поля профиля: текст — спрашиваем значение; тон — мультивыбор."""
    await callback.answer()
    key = callback.data.rsplit(":", 1)[1]

    if key == "tone":
        profile = await brand_profile.get_by_user(callback.from_user.id) or {}
        current = [t.strip() for t in (profile.get("tone") or "").split("+") if t.strip()]
        await state.update_data(tones=current)
        await state.set_state(ProfileEdit.editing_tone)
        await callback.message.answer(
            "Выбери тон (можно несколько):",
            reply_markup=tone_keyboard(current),
        )
        return

    if key not in _FIELD_COLUMN:
        await callback.message.answer("Неизвестное поле.")
        return

    await state.update_data(edit_field=key)
    await state.set_state(ProfileEdit.editing_text)
    await callback.message.answer(_FIELD_PROMPT[key])


# ───────────────────────── Профиль: текстовое поле ─────────────────────────


@router.message(ProfileEdit.editing_text, F.text & ~F.text.startswith("/"))
async def on_profile_text(message: Message, state: FSMContext) -> None:
    """Принимает новое значение текстового поля и сохраняет его в профиль."""
    data = await state.get_data()
    key = data.get("edit_field")
    if key not in _FIELD_COLUMN:
        await state.clear()
        await message.answer("Что-то пошло не так. Открой профиль заново через меню.")
        return

    value = message.text.strip()

    # Поля, которые нельзя очищать
    if not value and key in _REQUIRED_FIELDS:
        await message.answer("Это поле не может быть пустым. Введи значение:")
        return

    # Для запрещённых слов допускаем явную очистку
    if key == "forbidden" and value in ("-", "—", "нет", "Нет"):
        value = ""

    column = _FIELD_COLUMN[key]
    update_kwargs: dict = {column: value or None}

    # Референсы влияют на style_description (он уходит в системный промпт)
    if key == "refs":
        profile = await brand_profile.get_by_user(message.from_user.id) or {}
        update_kwargs["style_description"] = onboarding.build_style_description(
            profile.get("tone") or "", value
        )

    try:
        await brand_profile.update(message.from_user.id, **update_kwargs)
    except Exception:
        logger.exception("Ошибка обновления профиля (user=%s)", message.from_user.id)
        await message.answer("Не получилось сохранить. Попробуй ещё раз.")
        await alert_owner(message.bot, "Ошибка обновления профиля в БД.")
        return

    await state.clear()
    await message.answer(f"{_FIELD_TITLE[key]} — обновлено ✓")
    await _show_profile(message, message.from_user.id)


# ───────────────────────── Профиль: мультивыбор тона ─────────────────────────


@router.callback_query(ProfileEdit.editing_tone, F.data.startswith("tone:"))
async def on_profile_tone_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключение варианта тона при редактировании (галочки)."""
    option = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected: list[str] = list(data.get("tones", []))

    if option in selected:
        selected.remove(option)
    else:
        selected.append(option)

    await state.update_data(tones=selected)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=tone_keyboard(selected))


@router.callback_query(ProfileEdit.editing_tone, F.data == "tone_done")
async def on_profile_tone_done(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохранение выбранного тона (+ синхронизация style_description)."""
    data = await state.get_data()
    selected: list[str] = list(data.get("tones", []))
    if not selected:
        await callback.answer("Выбери хотя бы один вариант", show_alert=True)
        return

    await callback.answer()
    tone_str = " + ".join(selected)
    profile = await brand_profile.get_by_user(callback.from_user.id) or {}
    style = onboarding.build_style_description(tone_str, profile.get("reference_accounts") or "")

    try:
        await brand_profile.update(
            callback.from_user.id, tone=tone_str, style_description=style
        )
    except Exception:
        logger.exception("Ошибка обновления тона (user=%s)", callback.from_user.id)
        await callback.message.answer("Не получилось сохранить. Попробуй ещё раз.")
        await alert_owner(callback.bot, "Ошибка обновления профиля (тон) в БД.")
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.clear()
    await callback.message.answer("Тон — обновлено ✓")
    await _show_profile(callback.message, callback.from_user.id)
