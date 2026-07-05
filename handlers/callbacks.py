"""Колбэки потока генерации поста (Фаза 6, spec.md §7.2, §9.1, §10).

Поток (после показа 3 хуков из handlers/message.py):
  post_hook:<idx>    → ШАГ 2: генерируем полный пост по выбранному хуку,
                       форматируем, шлём с кнопками действий, состояние viewing_post.
  post:rewrite       → перегенерировать пост с тем же хуком (та же температура).
  post:another       → «Другой вариант»: новый пост по тому же хуку, температура выше.
  post:another_hook  → снова показать сохранённые 3 хука (без нового вызова LLM).
  post:save          → сохранить в drafts + записать тему в publications (дедуп §13.2).
  menu / menu:main   → сбросить FSM, показать главное меню.
  menu:post          → запустить поток поста (спросить тему).

Конфликт «menu:*» со start.py решён так: start.py обрабатывает только
menu:reel/drafts/profile (заглушки), а menu:post и menu (🏠 В меню) — здесь.
Роутер этого модуля регистрируется в main.py ПОСЛЕ start и message.

Сессия поста хранится в FSM data (topic, hooks, selected_hook, last_post_text),
а состояния PostFlow.choosing_hook/viewing_post защищают от того, чтобы
свободный текст случайно реролил поток (см. handlers/message.py — он работает
только на StateFilter(None)).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from agents import post_writer, reel_writer
from database import drafts, publications, style_feedback
from prompts.post_prompt import POST_TEMPERATURE
from prompts.reel_prompt import DEFAULT_DURATION, REEL_TEMPERATURE, SUPPORTED_DURATIONS
from utils.errors import GenerationResult, alert_owner
from utils.formatter import _escape_md, format_post, format_reel
from utils.keyboards import (
    hooks_keyboard,
    main_menu_keyboard,
    post_actions_keyboard,
    reel_actions_keyboard,
    reel_hooks_keyboard,
    reel_length_keyboard,
)
from utils.logger import get_logger
from utils.progress import Progress
from utils.state import PostFlow, ReelFlow

logger = get_logger(__name__)

# Роутер потока поста — регистрируется в main.py ПОСЛЕ start и message.
router = Router(name="callbacks")

# Текст приглашения ввести тему после кнопки меню «✍️ Написать пост».
ASK_POST_TOPIC = "О чём написать пост?"

# Подпись над главным меню (как в start.py).
MENU_PROMPT = "Чем займёмся?"

# Сообщение об устаревшей/потерянной сессии (нажали кнопку после рестарта и т.п.).
SESSION_LOST = "Сессия устарела. Напиши тему поста заново 🙏"

# Статус-сообщения на время генерации (заменяются готовым результатом).
WRITING_POST = "✍️ Пишу пост… это займёт 10–20 секунд"
WRITING_REEL = "🎬 Пишу сценарий… это займёт 10–20 секунд"

# Повышенная температура для кнопки «💡 Другой вариант» (больше вариативности).
_ANOTHER_TEMPERATURE = min(POST_TEMPERATURE + 0.2, 1.0)


# ───────────────────────── Хелперы ─────────────────────────


async def send_typing(callback_or_message) -> None:
    """Показывает индикатор «печатает…» перед вызовом LLM (spec.md §7.2).

    Принимает CallbackQuery или Message — берёт bot и chat_id из объекта.
    Любые ошибки отправки экшена игнорируются (индикатор не критичен).
    """
    try:
        bot = callback_or_message.bot
        # У CallbackQuery чат лежит в .message, у Message — это сам объект
        msg = getattr(callback_or_message, "message", None) or callback_or_message
        await bot.send_chat_action(chat_id=msg.chat.id, action="typing")
    except Exception:  # индикатор не должен ронять поток
        logger.debug("Не удалось отправить chat_action typing", exc_info=True)


async def _alert_owner_on_failure(
    callback: CallbackQuery, result: GenerationResult, *, label: str
) -> None:
    """Короткий алерт владельцу при неуспешной генерации (§6.8), если каталог требует.

    Пользователю текст ошибки показывает прогресс-статус (progress.fail); здесь —
    только уведомление владельцу. label — что генерировали («поста»/«Reels»).
    """
    if result.alert_owner:
        await alert_owner(callback.bot, f"Сбой генерации {label} [{result.situation}].")


async def _generate_and_show_post(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    temperature: float,
) -> None:
    """ШАГ 2: генерирует пост по выбранному хуку из сессии и показывает его.

    Используется для post_hook:<idx>, post:rewrite и post:another (с разной
    температурой). Достаёт профиль/историю/тему/хук из FSM data, вызывает
    generate_post, форматирует и отправляет с кнопками действий. Сохраняет
    сырой ответ в last_post_text и ставит состояние viewing_post.
    """
    data = await state.get_data()
    topic: str = data.get("topic", "")
    hooks: list[str] = list(data.get("hooks", []))
    selected: int | None = data.get("selected_hook")

    if not topic or selected is None or selected >= len(hooks):
        # Сессия потеряна/неконсистентна — просим начать заново.
        await callback.message.answer(SESSION_LOST)
        await state.clear()
        return

    selected_hook = hooks[selected]

    # Профиль, история тем и примеры стиля — в каждый системный промпт (§13.1/§13.2/§13.3).
    profile, recent_topics, style_examples = await _load_profile_and_topics(callback.from_user.id)

    # Статус-сообщение на время генерации — заменится готовым постом (или ошибкой).
    progress = await Progress.begin(callback.message, WRITING_POST)
    result = await post_writer.generate_post(
        profile=profile,
        recent_topics=recent_topics,
        topic=topic,
        selected_hook=selected_hook,
        bot=callback.bot,
        temperature=temperature,
        style_examples=style_examples,
    )

    if not result.ok:
        await progress.fail(result.user_message)
        await _alert_owner_on_failure(callback, result, label="поста")
        return

    # Сохраняем сырой ответ в сессию (для сохранения в БД и истории действий)
    await state.update_data(last_post_text=result.text)
    await state.set_state(PostFlow.viewing_post)

    pretty = format_post(result.text)
    await progress.finish(pretty, reply_markup=post_actions_keyboard())


async def _load_profile_and_topics(user_id: int):
    """Загружает профиль, последние темы и примеры стиля (для system-промпта).

    Примеры стиля (V2, §13.3) — одобренный/отклонённый контент из style_feedback;
    подмешиваются в системный промпт, чтобы бот точнее попадал в голос владельца.
    """
    from database import brand_profile, style_feedback  # локальный импорт во избежание циклов

    profile = await brand_profile.get_by_user(user_id) or {}
    recent_topics = await publications.get_recent_topics(user_id, limit=20)
    style_examples = await style_feedback.get_examples(user_id)
    return profile, recent_topics, style_examples


# ───────────────────── Запуск потока поста (menu:post) ─────────────────────


@router.callback_query(F.data == "menu:post")
async def on_menu_post(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка меню «✍️ Написать пост»: спрашиваем тему и ждём её текстом.

    Ставим состояние waiting_topic — тему ловит handlers/message.py
    (обработчик PostFlow.waiting_topic).
    """
    await callback.answer()
    await state.clear()
    await state.set_state(PostFlow.waiting_topic)
    await callback.message.answer(ASK_POST_TOPIC)


# ───────────────────── Выбор хука (post_hook:<idx>) ─────────────────────


@router.callback_query(PostFlow.choosing_hook, F.data.startswith("post_hook:"))
async def on_hook_selected(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал хук → генерируем полный пост (ШАГ 2)."""
    await callback.answer()
    try:
        idx = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer(SESSION_LOST)
        return

    data = await state.get_data()
    hooks: list[str] = list(data.get("hooks", []))
    if idx < 0 or idx >= len(hooks):
        await callback.message.answer(SESSION_LOST)
        return

    # Запоминаем выбранный хук и убираем кнопки выбора у предыдущего сообщения
    await state.update_data(selected_hook=idx)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _generate_and_show_post(callback, state, temperature=POST_TEMPERATURE)


# ───────────────────── Действия под постом ─────────────────────


@router.callback_query(PostFlow.viewing_post, F.data == "post:rewrite")
async def on_post_rewrite(callback: CallbackQuery, state: FSMContext) -> None:
    """🔄 Переписать: перегенерировать пост с тем же хуком (та же температура)."""
    await callback.answer("Переписываю…")
    await _generate_and_show_post(callback, state, temperature=POST_TEMPERATURE)


@router.callback_query(PostFlow.viewing_post, F.data == "post:another")
async def on_post_another(callback: CallbackQuery, state: FSMContext) -> None:
    """💡 Другой вариант: новый пост по тому же хуку с большей вариативностью."""
    await callback.answer("Делаю другой вариант…")
    await _generate_and_show_post(callback, state, temperature=_ANOTHER_TEMPERATURE)


@router.callback_query(PostFlow.viewing_post, F.data == "post:another_hook")
async def on_post_another_hook(callback: CallbackQuery, state: FSMContext) -> None:
    """🪝 Другой хук: снова показать сохранённые 3 хука (без нового вызова LLM)."""
    await callback.answer()
    data = await state.get_data()
    hooks: list[str] = list(data.get("hooks", []))
    if not hooks:
        await callback.message.answer(SESSION_LOST)
        await state.clear()
        return

    # Возвращаемся к выбору хука
    await state.set_state(PostFlow.choosing_hook)
    text = _render_hooks_message(hooks)
    await callback.message.answer(text, reply_markup=hooks_keyboard(hooks))


@router.callback_query(PostFlow.viewing_post, F.data == "post:save")
async def on_post_save(callback: CallbackQuery, state: FSMContext) -> None:
    """💾 Сохранить: drafts.save + publications.save (тема → история, дедуп §13.2)."""
    data = await state.get_data()
    topic: str = data.get("topic", "")
    hooks: list[str] = list(data.get("hooks", []))
    selected: int | None = data.get("selected_hook")
    content: str = data.get("last_post_text", "")

    if not content or not topic:
        await callback.answer(SESSION_LOST, show_alert=True)
        return

    try:
        # Черновик: сохраняем сырой пост + все хуки (JSON внутри drafts.save)
        await drafts.save(
            user_telegram_id=callback.from_user.id,
            content_type="post",
            topic=topic,
            content=content,
            hooks=hooks,
            status="draft",
        )
        # История публикаций: тема для дедупликации (§13.2) + выбранный хук
        await publications.save(
            user_telegram_id=callback.from_user.id,
            content_type="post",
            topic=topic,
            content=content,
            hooks=hooks,
            selected_hook=selected,
        )
    except Exception:
        # Ошибку БД мапим в дружелюбный ответ + алерт владельцу (§6.8 / каталог)
        logger.exception("Ошибка сохранения поста (user=%s)", callback.from_user.id)
        await callback.answer("Не получилось сохранить. Попробуй ещё раз.", show_alert=True)
        await alert_owner(callback.bot, "Ошибка сохранения поста в БД.")
        return

    await callback.answer("Сохранено ✓")
    await callback.message.answer("Сохранено ✓")


# ───────────────────── Обучение стилю: 👍/👎 (V2, spec.md §13.3) ─────────────────────

# Тексты подтверждения оценки.
_APPROVED_ACK = "Запомнил — буду писать в таком стиле 👍"
_REJECTED_ACK = "Понял — буду избегать такого 👎"


async def _save_style_feedback(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    data_key: str,
    verdict: str,
) -> None:
    """Сохраняет последний показанный контент в style_feedback с вердиктом.

    data_key — ключ FSM data с сырым текстом (last_post_text / last_reel_text).
    Накопленные примеры подмешиваются в будущие промпты (build_system_prompt).
    Ошибку БД мапим в дружелюбный ответ (оценка не критична для потока).
    """
    data = await state.get_data()
    content: str = data.get(data_key, "")
    if not content:
        await callback.answer(SESSION_LOST, show_alert=True)
        return

    try:
        await style_feedback.save(
            user_telegram_id=callback.from_user.id,
            content_snippet=content,
            verdict=verdict,
        )
    except Exception:
        logger.exception("Ошибка сохранения оценки стиля (user=%s)", callback.from_user.id)
        await callback.answer("Не получилось сохранить оценку. Попробуй ещё раз.", show_alert=True)
        await alert_owner(callback.bot, "Ошибка сохранения style_feedback в БД.")
        return

    ack = _APPROVED_ACK if verdict == style_feedback.APPROVED else _REJECTED_ACK
    await callback.answer(ack)


@router.callback_query(PostFlow.viewing_post, F.data == "post:approve")
async def on_post_approve(callback: CallbackQuery, state: FSMContext) -> None:
    """👍 Одобрить пост → сохранить как пример хорошего стиля (§13.3)."""
    await _save_style_feedback(
        callback, state, data_key="last_post_text", verdict=style_feedback.APPROVED
    )


@router.callback_query(PostFlow.viewing_post, F.data == "post:reject")
async def on_post_reject(callback: CallbackQuery, state: FSMContext) -> None:
    """👎 Отклонить пост → сохранить как пример плохого стиля (§13.3)."""
    await _save_style_feedback(
        callback, state, data_key="last_post_text", verdict=style_feedback.REJECTED
    )


# ═════════════════════ Поток сценария Reels (Фаза 7, spec.md §7.3) ═════════════════════

# Тексты приглашения/потери сессии для потока Reels.
ASK_REEL_TOPIC = "О чём снять Reels?"
SESSION_LOST_REEL = "Сессия устарела. Напиши тему Reels заново 🙏"


async def _generate_and_show_reel(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    temperature: float = REEL_TEMPERATURE,
) -> None:
    """ШАГ 2 потока Reels: генерирует сценарий по выбранному хуку и длине, показывает.

    Используется для reel_hook:<idx>, reel:rewrite и reel:len:<sec>. Достаёт
    профиль/историю/тему/хук/длину из FSM data, вызывает generate_reel,
    форматирует и отправляет с кнопками действий. Сохраняет сырой ответ в
    last_reel_text и ставит состояние viewing_reel.
    """
    data = await state.get_data()
    topic: str = data.get("topic", "")
    hooks: list[str] = list(data.get("hooks", []))
    selected: int | None = data.get("selected_hook")
    duration: int = data.get("duration", DEFAULT_DURATION)

    if not topic or selected is None or selected >= len(hooks):
        await callback.message.answer(SESSION_LOST_REEL)
        await state.clear()
        return

    selected_hook = hooks[selected]
    profile, recent_topics, style_examples = await _load_profile_and_topics(callback.from_user.id)

    # Статус-сообщение на время генерации — заменится готовым сценарием (или ошибкой).
    progress = await Progress.begin(callback.message, WRITING_REEL)
    result = await reel_writer.generate_reel(
        profile=profile,
        recent_topics=recent_topics,
        topic=topic,
        selected_hook=selected_hook,
        duration_sec=duration,
        bot=callback.bot,
        temperature=temperature,
        style_examples=style_examples,
    )

    if not result.ok:
        await progress.fail(result.user_message)
        await _alert_owner_on_failure(callback, result, label="Reels")
        return

    await state.update_data(last_reel_text=result.text)
    await state.set_state(ReelFlow.viewing_reel)

    pretty = format_reel(result.text)
    await progress.finish(pretty, reply_markup=reel_actions_keyboard())


@router.callback_query(F.data == "menu:reel")
async def on_menu_reel(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка меню «🎬 Сценарий Reels»: спрашиваем тему и ждём её текстом.

    Ставим ReelFlow.waiting_topic — тему ловит handlers/message.py.
    """
    await callback.answer()
    await state.clear()
    await state.set_state(ReelFlow.waiting_topic)
    await callback.message.answer(ASK_REEL_TOPIC)


@router.callback_query(ReelFlow.choosing_hook, F.data.startswith("reel_hook:"))
async def on_reel_hook_selected(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал хук → генерируем сценарий Reels (ШАГ 2)."""
    await callback.answer()
    try:
        idx = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer(SESSION_LOST_REEL)
        return

    data = await state.get_data()
    hooks: list[str] = list(data.get("hooks", []))
    if idx < 0 or idx >= len(hooks):
        await callback.message.answer(SESSION_LOST_REEL)
        return

    # Запоминаем выбранный хук; длина — дефолтная, если ещё не выбрана.
    duration = data.get("duration", DEFAULT_DURATION)
    await state.update_data(selected_hook=idx, duration=duration)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _generate_and_show_reel(callback, state)


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:rewrite")
async def on_reel_rewrite(callback: CallbackQuery, state: FSMContext) -> None:
    """🔄 Переписать: перегенерировать сценарий с тем же хуком и длиной."""
    await callback.answer("Переписываю…")
    await _generate_and_show_reel(callback, state)


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:another_hook")
async def on_reel_another_hook(callback: CallbackQuery, state: FSMContext) -> None:
    """🪝 Другой хук: снова показать сохранённые 3 хука (без нового вызова LLM)."""
    await callback.answer()
    data = await state.get_data()
    hooks: list[str] = list(data.get("hooks", []))
    if not hooks:
        await callback.message.answer(SESSION_LOST_REEL)
        await state.clear()
        return

    await state.set_state(ReelFlow.choosing_hook)
    text = _render_hooks_message(hooks)
    await callback.message.answer(text, reply_markup=reel_hooks_keyboard(hooks))


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:length")
async def on_reel_length(callback: CallbackQuery, state: FSMContext) -> None:
    """📐 Изменить длину: показать выбор 15/30/60 сек (текущая отмечена ✓)."""
    await callback.answer()
    data = await state.get_data()
    current = data.get("duration", DEFAULT_DURATION)
    await callback.message.answer(
        "Выбери длину Reels:",
        reply_markup=reel_length_keyboard(current),
    )


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:back")
async def on_reel_length_back(callback: CallbackQuery) -> None:
    """← Назад из выбора длины: просто убираем клавиатуру выбора длины."""
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(ReelFlow.viewing_reel, F.data.startswith("reel:len:"))
async def on_reel_set_length(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор длины (15/30/60): сохраняем и пересобираем сценарий под неё."""
    try:
        sec = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return
    if sec not in SUPPORTED_DURATIONS:
        await callback.answer()
        return

    await callback.answer(f"Пересобираю на {sec} сек…")
    await state.update_data(duration=sec)
    # Убираем клавиатуру выбора длины у сообщения-приглашения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _generate_and_show_reel(callback, state)


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:save")
async def on_reel_save(callback: CallbackQuery, state: FSMContext) -> None:
    """💾 Сохранить: drafts.save + publications.save (тема → история, дедуп §13.2)."""
    data = await state.get_data()
    topic: str = data.get("topic", "")
    hooks: list[str] = list(data.get("hooks", []))
    selected: int | None = data.get("selected_hook")
    content: str = data.get("last_reel_text", "")

    if not content or not topic:
        await callback.answer(SESSION_LOST_REEL, show_alert=True)
        return

    try:
        await drafts.save(
            user_telegram_id=callback.from_user.id,
            content_type="reel",
            topic=topic,
            content=content,
            hooks=hooks,
            status="draft",
        )
        await publications.save(
            user_telegram_id=callback.from_user.id,
            content_type="reel",
            topic=topic,
            content=content,
            hooks=hooks,
            selected_hook=selected,
        )
    except Exception:
        logger.exception("Ошибка сохранения Reels (user=%s)", callback.from_user.id)
        await callback.answer("Не получилось сохранить. Попробуй ещё раз.", show_alert=True)
        await alert_owner(callback.bot, "Ошибка сохранения Reels в БД.")
        return

    await callback.answer("Сохранено ✓")
    await callback.message.answer("Сохранено ✓")


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:approve")
async def on_reel_approve(callback: CallbackQuery, state: FSMContext) -> None:
    """👍 Одобрить сценарий → сохранить как пример хорошего стиля (§13.3)."""
    await _save_style_feedback(
        callback, state, data_key="last_reel_text", verdict=style_feedback.APPROVED
    )


@router.callback_query(ReelFlow.viewing_reel, F.data == "reel:reject")
async def on_reel_reject(callback: CallbackQuery, state: FSMContext) -> None:
    """👎 Отклонить сценарий → сохранить как пример плохого стиля (§13.3)."""
    await _save_style_feedback(
        callback, state, data_key="last_reel_text", verdict=style_feedback.REJECTED
    )


# ───────────────────── В меню (menu / menu:main) ─────────────────────


@router.callback_query(F.data.in_({"menu", "menu:main"}))
async def on_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """🏠 В меню: сбросить FSM и показать главное меню."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(MENU_PROMPT, reply_markup=main_menu_keyboard())


# ───────────────────── Вспомогательное ─────────────────────


def _render_hooks_message(hooks: list[str]) -> str:
    """Собирает текст сообщения с тремя вариантами хука (как в §7.2)."""
    lines = ["✍️ *ТРИ ВАРИАНТА ХУКА* — выбери один:", ""]
    for idx, hook in enumerate(hooks, start=1):
        # Хуки — вывод модели, могут содержать * _ ` [ → экранируем, иначе
        # непарный символ ломает разбор Markdown в Telegram (TelegramBadRequest).
        lines.append(f"*{idx}.* {_escape_md(hook)}")
    return "\n".join(lines)
