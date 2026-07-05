"""Хэндлер свободного текста → оркестратор → маршрут (spec.md §6.1, §14).

Поток:
  свободный текст → гард профиля (§14) → detect_intent → маршрут:
    • post  → выделить тему → точка вызова Фазы 6 (handle_post_request);
    • reel  → выделить тему → точка вызова Фазы 7 (handle_reel_request);
    • plan/trend → «Это будет в следующей версии (V2).»;
    • unknown → вежливое уточнение (§6.1) + главное меню.

ВАЖНО: хэндлер срабатывает ТОЛЬКО когда нет активного состояния онбординга —
фильтр StateFilter(None). Команды (/start) обрабатываются в handlers/start.py.
Роутер этого модуля регистрируется в main.py ПОСЛЕ start.router.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from agents import post_writer, reel_writer
from agents.orchestrator import CLARIFY_TEXT, detect_intent, extract_topic
from database import brand_profile, publications, style_feedback
from handlers.callbacks import _render_hooks_message
from utils.keyboards import hooks_keyboard, main_menu_keyboard, reel_hooks_keyboard
from utils.logger import get_logger
from utils.progress import Progress
from utils.state import PostFlow, ReelFlow

logger = get_logger(__name__)

# Роутер свободного текста — регистрируется в main.py ПОСЛЕ start.router.
router = Router(name="message")

# Гард профиля (§14): онбординг не завершён → вернуть в онбординг.
NO_PROFILE_TEXT = "Нужно сначала настроить профиль. Нажми /start"

# Уточнение темы, если intent распознан, но тема пустая.
ASK_POST_TOPIC = "О чём написать пост?"
ASK_REEL_TOPIC = "О чём снять Reels?"


# ───────────────────── Точки расширения для Фаз 6/7 ─────────────────────


# Сообщение, если модель не вернула достаточно хуков.
_HOOKS_FAILED = "Не получилось придумать варианты. Попробуй переформулировать тему 🙏"

# Статус-сообщения на время генерации хуков (заменяются вариантами или ошибкой).
WRITING_POST_HOOKS = "✍️ Придумываю 3 варианта хука…"
WRITING_REEL_HOOKS = "🎬 Придумываю 3 варианта хука…"


async def handle_post_request(message: Message, topic: str, state: FSMContext) -> None:
    """ШАГ 1 потока поста (Фаза 6, §7.2): генерируем 3 хука по теме.

    Загружает профиль + историю тем, показывает «печатает…», вызывает
    generate_hooks, парсит ответ. При успехе сохраняет сессию в FSM data
    (topic, hooks), ставит состояние choosing_hook и показывает 3 кнопки хуков.
    Ошибки LLM → RU-сообщение из GenerationResult (без тех.текста).
    """
    user_id = message.from_user.id
    logger.info("handle_post_request: тема=%r", topic)

    # Профиль + последние темы (дедуп §13.2) + примеры стиля (§13.3) — в системный промпт.
    profile = await brand_profile.get_by_user(user_id) or {}
    recent_topics = await publications.get_recent_topics(user_id, limit=20)
    style_examples = await style_feedback.get_examples(user_id)

    # Статус-сообщение на время генерации — заменится вариантами хука (или ошибкой).
    progress = await Progress.begin(message, WRITING_POST_HOOKS)
    result = await post_writer.generate_hooks(
        profile=profile,
        recent_topics=recent_topics,
        topic=topic,
        bot=message.bot,
        style_examples=style_examples,
    )

    # Ошибка генерации → дружелюбное RU-сообщение (+ алерт владельцу при необходимости)
    if not result.ok:
        await progress.fail(result.user_message)
        if result.alert_owner:
            from utils.errors import alert_owner

            await alert_owner(message.bot, f"Сбой генерации хуков [{result.situation}].")
        await state.clear()
        return

    hooks = post_writer.parse_hooks(result.text)
    if not hooks:
        # Совсем не распарсилось — просим переформулировать
        await progress.fail(_HOOKS_FAILED)
        await state.clear()
        return

    # Сохраняем сессию поста и переходим к выбору хука (свободный текст не реролит)
    await state.update_data(topic=topic, hooks=hooks)
    await state.set_state(PostFlow.choosing_hook)
    await progress.finish(_render_hooks_message(hooks), reply_markup=hooks_keyboard(hooks))


async def handle_reel_request(message: Message, topic: str, state: FSMContext) -> None:
    """ШАГ 1 потока Reels (Фаза 7, §7.3): генерируем 3 хука по теме.

    Зеркалит handle_post_request: загружает профиль + историю тем, показывает
    «печатает…», вызывает reel_writer.generate_hooks, парсит ответ. При успехе
    сохраняет сессию в FSM data (topic, hooks), ставит ReelFlow.choosing_hook и
    показывает 3 кнопки хуков. Ошибки LLM → RU-сообщение (без тех.текста).
    """
    user_id = message.from_user.id
    logger.info("handle_reel_request: тема=%r", topic)

    profile = await brand_profile.get_by_user(user_id) or {}
    recent_topics = await publications.get_recent_topics(user_id, limit=20)
    style_examples = await style_feedback.get_examples(user_id)

    # Статус-сообщение на время генерации — заменится вариантами хука (или ошибкой).
    progress = await Progress.begin(message, WRITING_REEL_HOOKS)
    result = await reel_writer.generate_hooks(
        profile=profile,
        recent_topics=recent_topics,
        topic=topic,
        bot=message.bot,
        style_examples=style_examples,
    )

    if not result.ok:
        await progress.fail(result.user_message)
        if result.alert_owner:
            from utils.errors import alert_owner

            await alert_owner(message.bot, f"Сбой генерации хуков Reels [{result.situation}].")
        await state.clear()
        return

    hooks = reel_writer.parse_hooks(result.text)
    if not hooks:
        await progress.fail(_HOOKS_FAILED)
        await state.clear()
        return

    # Сохраняем сессию Reels и переходим к выбору хука (свободный текст не реролит)
    await state.update_data(topic=topic, hooks=hooks)
    await state.set_state(ReelFlow.choosing_hook)
    await progress.finish(_render_hooks_message(hooks), reply_markup=reel_hooks_keyboard(hooks))


# ───────────────────── Тема поста после кнопки меню «✍️ Написать пост» ─────────────────────


@router.message(PostFlow.waiting_topic, F.text & ~F.text.startswith("/"))
async def on_post_topic(message: Message, state: FSMContext) -> None:
    """Принимает тему поста (после menu:post → PostFlow.waiting_topic).

    Берём текст как тему целиком (пользователь уже понял, что вводит тему), без
    повторной вычистки командных слов, и запускаем ШАГ 1.
    """
    topic = message.text.strip()
    if not topic:
        await message.answer(ASK_POST_TOPIC)
        return
    await handle_post_request(message, topic, state)


# ───────────────────── Тема Reels после кнопки меню «🎬 Сценарий Reels» ─────────────────────


@router.message(ReelFlow.waiting_topic, F.text & ~F.text.startswith("/"))
async def on_reel_topic(message: Message, state: FSMContext) -> None:
    """Принимает тему Reels (после menu:reel → ReelFlow.waiting_topic) и запускает ШАГ 1."""
    topic = message.text.strip()
    if not topic:
        await message.answer(ASK_REEL_TOPIC)
        return
    await handle_reel_request(message, topic, state)


# ───────────────────── Хэндлер свободного текста ─────────────────────


async def route_text(message: Message, text: str, state: FSMContext) -> None:
    """Маршрутизация произвольного текста (свободный текст ИЛИ расшифрованный голос).

    Гард профиля (§14) → detect_intent → маршрут к посту/Reels/V2/уточнению.
    Вынесено из handle_free_text, чтобы голосовой хэндлер (Фаза 9) переиспользовал
    ту же логику, передавая расшифрованный Whisper текст.
    """
    user_id = message.from_user.id

    # Гард профиля (§14): нет профиля → в онбординг, дальше не роутим.
    if not await brand_profile.exists(user_id):
        logger.info("Текст без профиля (user=%s) → онбординг", user_id)
        await message.answer(NO_PROFILE_TEXT)
        return

    intent = detect_intent(text)

    if intent == "post":
        topic = extract_topic(text, intent)
        if not topic:
            await message.answer(ASK_POST_TOPIC)
            return
        await handle_post_request(message, topic, state)
        return

    if intent == "reel":
        topic = extract_topic(text, intent)
        if not topic:
            await message.answer(ASK_REEL_TOPIC)
            return
        await handle_reel_request(message, topic, state)
        return

    if intent == "plan":
        from handlers.plan import start_plan_flow

        await start_plan_flow(message, state, user_id)
        return

    if intent == "trend":
        from handlers.trends import start_trend_flow

        await start_trend_flow(message, state, user_id)
        return

    # unknown → вежливое уточнение (§6.1) + главное меню для удобства.
    await message.answer(CLARIFY_TEXT, reply_markup=main_menu_keyboard())


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_free_text(message: Message, state: FSMContext) -> None:
    """Свободный текст вне онбординга → определение intent → маршрут."""
    await route_text(message, message.text, state)
