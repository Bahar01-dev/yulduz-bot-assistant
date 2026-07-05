"""Анализ трендов/конкурентов (V2, spec.md §18 «Тренды», §17.1).

Поток:
  /тренды или кнопка меню «📈 Тренды» (menu:trend)
    → generate_trends (Tavily веб-поиск → разбор Claude)
    → показ разбора + кнопки [🔄 Обновить][🏠 В меню]
    → trend:refresh — собрать заново.

Гейт по ключу: если TAVILY_API_KEY не задан (config.TRENDS_ENABLED=False) —
показываем мягкое сообщение «подключи ключ», поток не запускается (как голос
без GROQ). Гард профиля (§14): без профиля → в онбординг.

Роутер регистрируется в main.py ПОСЛЕ остальных. Колбэки trend:* и menu:trend
не пересекаются с фильтрами других роутеров.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from agents import trend_analyst
from config import config
from database import brand_profile
from utils.formatter import format_trends
from utils.keyboards import trend_actions_keyboard
from utils.logger import get_logger
from utils.progress import Progress
from utils.state import TrendFlow

logger = get_logger(__name__)

router = Router(name="trends")

NO_PROFILE_TEXT = "Нужно сначала настроить профиль. Нажми /start"
TRENDS_DISABLED_TEXT = (
    "Анализ трендов появится, когда будет подключён ключ Tavily.\n"
    "Получи бесплатный ключ на app.tavily.com и добавь TAVILY_API_KEY "
    "в переменные окружения — после рестарта раздел заработает."
)
TRENDS_FAILED = "Не удалось собрать тренды сейчас. Попробуй чуть позже 🙏"
TRENDS_WORKING = "🔎 Собираю тренды… это займёт несколько секунд"


async def start_trend_flow(message: Message, state: FSMContext, user_id: int) -> None:
    """Запускает анализ трендов: гейт ключа + гард профиля → генерация.

    user_id передаётся явно: при вызове из колбэка message — сообщение бота.
    """
    if not config.TRENDS_ENABLED:
        await message.answer(TRENDS_DISABLED_TEXT)
        return
    if not await brand_profile.exists(user_id):
        await message.answer(NO_PROFILE_TEXT)
        return
    await state.clear()
    await _generate_and_show_trends(message, state, user_id)


async def _generate_and_show_trends(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    """Собирает тренды и показывает разбор с кнопками действий."""
    profile = await brand_profile.get_by_user(user_id) or {}

    # Статус-сообщение на время сбора трендов — заменится готовым разбором (или ошибкой).
    progress = await Progress.begin(message, TRENDS_WORKING)
    result = await trend_analyst.generate_trends(profile=profile, bot=message.bot)

    if not result.ok:
        await progress.fail(result.user_message)
        if result.alert_owner:
            from utils.errors import alert_owner

            await alert_owner(message.bot, f"Сбой анализа трендов [{result.situation}].")
        await state.clear()
        return

    pretty = format_trends(result.text)
    if not pretty:
        await progress.fail(TRENDS_FAILED)
        await state.clear()
        return

    await state.set_state(TrendFlow.viewing_trends)
    await progress.finish(pretty, reply_markup=trend_actions_keyboard())


# ───────────────────────── Точки входа ─────────────────────────


@router.message(Command("тренды", "trends"))
async def cmd_trends(message: Message, state: FSMContext) -> None:
    """Команда /тренды: запустить анализ трендов."""
    await start_trend_flow(message, state, message.from_user.id)


@router.callback_query(F.data == "menu:trend")
async def on_menu_trend(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка меню «📈 Тренды»: запустить анализ."""
    await callback.answer()
    await start_trend_flow(callback.message, state, callback.from_user.id)


@router.callback_query(TrendFlow.viewing_trends, F.data == "trend:refresh")
async def on_trend_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    """🔄 Обновить: собрать тренды заново."""
    await callback.answer("Обновляю…")
    await _generate_and_show_trends(callback.message, state, callback.from_user.id)
