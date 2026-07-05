"""Контент-план (V2, spec.md §18 «Контент-план на неделю/месяц»).

Поток:
  /план или кнопка меню «🗓 Контент-план» (menu:plan)
    → выбор периода (Неделя/Месяц)
    → plan:period:<week|month> → генерация плана через planner.generate_plan
    → показ плана + кнопки [🔄 Другой план][🏠 В меню]
    → plan:another — перегенерировать на тот же период.

Гард профиля (§14): без профиля → в онбординг. Ошибки LLM → RU-сообщение
(GenerationResult), как в потоках поста/Reels.

Роутер регистрируется в main.py ПОСЛЕ start/message/callbacks/menu. Колбэки
plan:* и menu:plan не пересекаются с фильтрами других роутеров.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from agents import planner
from database import brand_profile, publications, style_feedback
from prompts.plan_prompt import DEFAULT_PERIOD, PERIODS
from utils.formatter import format_plan
from utils.keyboards import plan_actions_keyboard, plan_period_keyboard
from utils.logger import get_logger
from utils.progress import Progress
from utils.state import PlanFlow

logger = get_logger(__name__)

router = Router(name="plan")

NO_PROFILE_TEXT = "Нужно сначала настроить профиль. Нажми /start"
ASK_PERIOD_TEXT = "На какой период составить контент-план?"
PLAN_FAILED = "Не получилось собрать план. Попробуй ещё раз 🙏"
WRITING_PLAN = "🗓 Собираю контент-план… это займёт 10–20 секунд"


async def start_plan_flow(message: Message, state: FSMContext, user_id: int) -> None:
    """Запускает поток плана: гард профиля → спросить период.

    Переиспользуется из команды /план, кнопки menu:plan и оркестратора
    (intent=plan из свободного текста). user_id передаётся явно, т.к. при вызове
    из колбэка message — это сообщение бота (message.from_user = бот).
    """
    if not await brand_profile.exists(user_id):
        await message.answer(NO_PROFILE_TEXT)
        return
    await state.clear()
    await state.set_state(PlanFlow.choosing_period)
    await message.answer(ASK_PERIOD_TEXT, reply_markup=plan_period_keyboard())


async def _generate_and_show_plan(
    message: Message,
    state: FSMContext,
    period: str,
    user_id: int,
) -> None:
    """Генерирует план на период и показывает его с кнопками действий."""
    profile = await brand_profile.get_by_user(user_id) or {}
    recent_topics = await publications.get_recent_topics(user_id, limit=20)
    style_examples = await style_feedback.get_examples(user_id)

    # Статус-сообщение на время генерации — заменится готовым планом (или ошибкой).
    progress = await Progress.begin(message, WRITING_PLAN)
    result = await planner.generate_plan(
        profile=profile,
        recent_topics=recent_topics,
        period=period,
        bot=message.bot,
        style_examples=style_examples,
    )

    if not result.ok:
        await progress.fail(result.user_message)
        if result.alert_owner:
            from utils.errors import alert_owner

            await alert_owner(message.bot, f"Сбой генерации плана [{result.situation}].")
        await state.clear()
        return

    pretty = format_plan(result.text, period)
    if not pretty:
        await progress.fail(PLAN_FAILED)
        await state.clear()
        return

    await state.update_data(period=period)
    await state.set_state(PlanFlow.viewing_plan)
    await progress.finish(pretty, reply_markup=plan_actions_keyboard())


# ───────────────────────── Точки входа ─────────────────────────


@router.message(Command("план", "plan"))
async def cmd_plan(message: Message, state: FSMContext) -> None:
    """Команда /план: запустить поток контент-плана."""
    await start_plan_flow(message, state, message.from_user.id)


@router.callback_query(F.data == "menu:plan")
async def on_menu_plan(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка меню «🗓 Контент-план»: спросить период."""
    await callback.answer()
    await start_plan_flow(callback.message, state, callback.from_user.id)


@router.callback_query(PlanFlow.choosing_period, F.data.startswith("plan:period:"))
async def on_plan_period(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбран период → сгенерировать и показать план."""
    await callback.answer()
    period = callback.data.rsplit(":", 1)[1]
    if period not in PERIODS:
        period = DEFAULT_PERIOD
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _generate_and_show_plan(callback.message, state, period, callback.from_user.id)


@router.callback_query(PlanFlow.viewing_plan, F.data == "plan:another")
async def on_plan_another(callback: CallbackQuery, state: FSMContext) -> None:
    """🔄 Другой план: перегенерировать на тот же период."""
    await callback.answer("Собираю другой план…")
    data = await state.get_data()
    period = data.get("period", DEFAULT_PERIOD)
    await _generate_and_show_plan(callback.message, state, period, callback.from_user.id)
