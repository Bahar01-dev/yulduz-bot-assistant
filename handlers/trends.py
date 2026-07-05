"""Трендовый бриф: по расписанию и вручную (V2.1, spec.md §20; V2 §18 «Тренды»).

Поток:
  /тренды или кнопка меню «📈 Тренды» (menu:trend)
    → generate_brief (Tavily веб-поиск → бриф Claude: 2-3 тренда + 3-5 тем)
    → кэш в trend_brief (за сегодня) + показ брифа с кнопками [Тема N] [🔁 Обновить]
    → тап по теме (trendtopic:*) обрабатывает handlers/callbacks.py: пост с
      grounding (реальные факты из того же поиска);
    → trend:refresh — пересобрать бриф за сегодня свежим поиском.

Утреннюю доставку того же брифа делает utils/scheduler.py (переиспользует
generate_brief + trend_brief.upsert + format_brief + brief_keyboard).

Гейт по ключу: если TAVILY_API_KEY не задан (config.TRENDS_ENABLED=False) —
показываем мягкое сообщение «подключи ключ», поток не запускается (как голос
без GROQ). Гард профиля (§14): без профиля → в онбординг.

Колбэки trend:refresh и menu:trend не пересекаются с фильтрами других роутеров;
роутер регистрируется в main.py ПОСЛЕ остальных.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from agents import trend_analyst
from config import config
from database import brand_profile, trend_brief
from utils.formatter import format_brief
from utils.keyboards import brief_keyboard
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
TRENDS_WORKING = "🔎 Собираю трендовый бриф… это займёт несколько секунд"


async def start_trend_flow(message: Message, state: FSMContext, user_id: int) -> None:
    """Запускает трендовый бриф: гейт ключа + гард профиля → сборка и показ.

    user_id передаётся явно: при вызове из колбэка message — сообщение бота.
    """
    if not config.TRENDS_ENABLED:
        await message.answer(TRENDS_DISABLED_TEXT)
        return
    if not await brand_profile.exists(user_id):
        await message.answer(NO_PROFILE_TEXT)
        return
    await state.clear()
    await _build_and_send_brief(message, state, user_id)


async def _build_and_send_brief(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    """Собирает бриф, кэширует его за сегодня и показывает с кнопками тем."""
    profile = await brand_profile.get_by_user(user_id) or {}

    # Статус-сообщение на время сбора — заменится готовым брифом (или ошибкой).
    progress = await Progress.begin(message, TRENDS_WORKING)
    result = await trend_analyst.generate_brief(profile)

    if not result.ok:
        await progress.fail(result.user_message or TRENDS_FAILED)
        if result.alert_owner:
            from utils.errors import alert_owner

            await alert_owner(message.bot, f"Сбой трендового брифа [{result.situation}].")
        await state.clear()
        return

    pretty = format_brief(result.brief_text)
    if not pretty:
        await progress.fail(TRENDS_FAILED)
        await state.clear()
        return

    # Кэш брифа за сегодня: grounding (raw_search) + темы. Тап по теме позже
    # берёт эти факты, не гоняя Tavily повторно. Ошибку записи не роняем в лицо
    # пользователю — бриф уже показан; логируем.
    brief_date = trend_brief.today_str()
    try:
        await trend_brief.upsert(brief_date, result.raw_search or "", result.topics)
    except Exception:
        logger.exception("Не удалось сохранить трендовый бриф за %s", brief_date)

    await state.set_state(TrendFlow.viewing_trends)
    await progress.finish(pretty, reply_markup=brief_keyboard(brief_date, result.topics))


# ───────────────────────── Точки входа ─────────────────────────


@router.message(Command("тренды", "trends"))
async def cmd_trends(message: Message, state: FSMContext) -> None:
    """Команда /тренды: собрать трендовый бриф."""
    await start_trend_flow(message, state, message.from_user.id)


@router.callback_query(F.data == "menu:trend")
async def on_menu_trend(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка меню «📈 Тренды»: собрать бриф."""
    await callback.answer()
    await start_trend_flow(callback.message, state, callback.from_user.id)


@router.callback_query(F.data == "trend:refresh")
async def on_trend_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    """🔁 Обновить: пересобрать бриф за сегодня свежим поиском.

    Без фильтра по состоянию — кнопка работает и под утренним брифом от
    планировщика (там FSM-состояние не выставлено). Гейт/гард повторяем.
    """
    await callback.answer("Обновляю…")
    if not config.TRENDS_ENABLED:
        await callback.message.answer(TRENDS_DISABLED_TEXT)
        return
    if not await brand_profile.exists(callback.from_user.id):
        await callback.message.answer(NO_PROFILE_TEXT)
        return
    await _build_and_send_brief(callback.message, state, callback.from_user.id)
