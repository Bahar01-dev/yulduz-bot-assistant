"""Хэндлер /start и онбординг (Фаза 4, spec.md §7.1).

Логика /start:
  - профиль есть     → сразу главное меню (онбординг не повторяется);
  - профиля нет      → приветствие + кнопка «Поехали →», старт FSM-онбординга.

FSM-поток онбординга (states из utils/state.py):
  niche → audience → tone (мультивыбор) → forbidden → refs → сохранение + резюме + меню.

Колбэки онбординга (onboarding_start, tone:*, tone_done, skip_forbidden)
обрабатываются в этом же роутере.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from agents import onboarding
from database import brand_profile
from handlers import is_owner  # noqa: F401  (гейт владельца — через middleware)
from utils.errors import GenerationResult, Situation, alert_owner
from utils.keyboards import (
    main_menu_keyboard,
    skip_forbidden_keyboard,
    start_onboarding_keyboard,
    tone_keyboard,
)
from utils.logger import get_logger
from utils.state import Onboarding

# Готовое RU-сообщение сбоя записи в БД — из каталога ошибок (Situation.DB_ERROR),
# чтобы онбординг реагировал на сбой так же, как все прочие записи в БД.
_DB_ERROR_TEXT = GenerationResult.failure(Situation.DB_ERROR).user_message

logger = get_logger(__name__)

# Роутер /start + онбординг — регистрируется в main.py
router = Router(name="start")

# Подпись над главным меню (spec.md §10, экран «после онбординга»)
MENU_PROMPT = "Чем займёмся?"


# ───────────────────────────── /start ─────────────────────────────


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    """/start: профиль есть → меню; профиля нет → старт онбординга."""
    user_id = message.from_user.id
    logger.info("Команда /start от user_id=%s", user_id)

    # Сбрасываем возможный недозавершённый онбординг
    await state.clear()

    if await brand_profile.exists(user_id):
        # Профиль уже есть — сразу главное меню, онбординг не повторяем
        logger.info("Профиль существует — открываю меню (user=%s)", user_id)
        await message.answer(MENU_PROMPT, reply_markup=main_menu_keyboard())
        return

    # Профиля нет — приветствие + кнопка «Поехали →»
    logger.info("Профиля нет — запускаю онбординг (user=%s)", user_id)
    await message.answer(
        onboarding.WELCOME_TEXT,
        reply_markup=start_onboarding_keyboard(),
    )


# ───────────────────── Старт онбординга (кнопка «Поехали») ─────────────────────


@router.callback_query(F.data == "onboarding_start")
async def on_onboarding_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Поехали →»: задаём первый вопрос и ставим состояние niche."""
    await callback.answer()  # гасим «часики»
    await state.set_state(Onboarding.niche)
    await callback.message.answer(onboarding.QUESTION_NICHE)


# ───────────────────── Шаг 1: ниша (текст) ─────────────────────


@router.message(Onboarding.niche, F.text)
async def on_niche(message: Message, state: FSMContext) -> None:
    """Принимает нишу, переходит к вопросу об аудитории."""
    await state.update_data(niche=message.text.strip())
    await state.set_state(Onboarding.audience)
    await message.answer(onboarding.QUESTION_AUDIENCE)


# ───────────────────── Шаг 2: аудитория (текст) ─────────────────────


@router.message(Onboarding.audience, F.text)
async def on_audience(message: Message, state: FSMContext) -> None:
    """Принимает аудиторию, переходит к мультивыбору тона."""
    await state.update_data(audience=message.text.strip())
    # Инициализируем список выбранных тонов в FSM data
    await state.update_data(tones=[])
    await state.set_state(Onboarding.tone)
    await message.answer(
        onboarding.QUESTION_TONE,
        reply_markup=tone_keyboard([]),
    )


# ───────────────────── Шаг 3: тон (мультивыбор inline) ─────────────────────


@router.callback_query(Onboarding.tone, F.data.startswith("tone:"))
async def on_tone_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключает (вкл/выкл) выбранный вариант тона и перерисовывает клавиатуру."""
    option = callback.data.split(":", 1)[1]

    data = await state.get_data()
    selected: list[str] = list(data.get("tones", []))

    # Тоггл: если уже выбран — убираем, иначе добавляем
    if option in selected:
        selected.remove(option)
    else:
        selected.append(option)

    await state.update_data(tones=selected)
    await callback.answer()
    # Перерисовываем клавиатуру с обновлёнными галочками
    await callback.message.edit_reply_markup(reply_markup=tone_keyboard(selected))


@router.callback_query(Onboarding.tone, F.data == "tone_done")
async def on_tone_done(callback: CallbackQuery, state: FSMContext) -> None:
    """Завершает выбор тона. Если ничего не выбрано — просит выбрать хотя бы один."""
    data = await state.get_data()
    selected: list[str] = list(data.get("tones", []))

    if not selected:
        # Нужен хотя бы один тон — не двигаемся дальше
        await callback.answer("Выбери хотя бы один вариант", show_alert=True)
        return

    await callback.answer()
    # Убираем клавиатуру выбора тона у предыдущего сообщения
    await callback.message.edit_reply_markup(reply_markup=None)
    # Переходим к шагу 4 (запрещённые слова) с кнопкой «Пропустить»
    await state.set_state(Onboarding.forbidden)
    await callback.message.answer(
        onboarding.QUESTION_FORBIDDEN,
        reply_markup=skip_forbidden_keyboard(),
    )


# ───────────────────── Шаг 4: запрещённые слова (текст / пропустить) ─────────────────────


@router.callback_query(Onboarding.forbidden, F.data == "skip_forbidden")
async def on_forbidden_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропуск шага 4: запрещённых слов нет, переходим к референсам."""
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(forbidden="")
    await state.set_state(Onboarding.refs)
    await callback.message.answer(onboarding.QUESTION_REFS)


@router.message(Onboarding.forbidden, F.text)
async def on_forbidden(message: Message, state: FSMContext) -> None:
    """Принимает запрещённые слова, переходит к референс-аккаунтам."""
    await state.update_data(forbidden=message.text.strip())
    await state.set_state(Onboarding.refs)
    await message.answer(onboarding.QUESTION_REFS)


# ───────────────────── Шаг 5: референс-аккаунты (текст) → сохранение ─────────────────────


@router.message(Onboarding.refs, F.text)
async def on_refs(message: Message, state: FSMContext) -> None:
    """Принимает референсы, сохраняет профиль, показывает резюме + меню."""
    user_id = message.from_user.id
    await state.update_data(refs=message.text.strip())

    data = await state.get_data()
    niche = data.get("niche", "")
    audience = data.get("audience", "")
    tones: list[str] = list(data.get("tones", []))
    forbidden = data.get("forbidden", "")
    refs = data.get("refs", "")

    # Мультивыбор тона объединяем в строку через « + »
    tone_str = " + ".join(tones)
    # Краткое описание стиля: тон + ориентиры (используется в промптах Фаз 6/7)
    style_description = onboarding.build_style_description(tone_str, refs)

    # Сохраняем профиль в БД. Единственная запись онбординга — защищаем так же,
    # как все прочие записи (menu.py/callbacks.py): при сбое БД показываем
    # дружелюбное RU-сообщение из каталога, шлём алерт владельцу и НЕ чистим FSM,
    # чтобы повторная отправка референсов ретраила сохранение (а не переигрывала
    # все 5 шагов). state.clear() и резюме — только после успеха.
    try:
        await brand_profile.create(
            user_telegram_id=user_id,
            niche=niche,
            target_audience=audience,
            tone=tone_str,
            forbidden_words=forbidden or None,
            reference_accounts=refs or None,
            style_description=style_description or None,
        )
    except Exception:
        logger.exception("Ошибка сохранения профиля при онбординге (user=%s)", user_id)
        await message.answer(_DB_ERROR_TEXT)
        await alert_owner(message.bot, "Ошибка сохранения профиля (онбординг) в БД.")
        return

    logger.info("Онбординг завершён, профиль сохранён (user=%s)", user_id)

    # Очищаем FSM — онбординг окончен
    await state.clear()

    # Резюме (дословно §7.1) + главное меню.
    # parse_mode=None: резюме подставляет сырой пользовательский текст (ниша,
    # аудитория, референс-ссылки с «_», «?», «&»), который ломает разбор Markdown
    # в Telegram (TelegramBadRequest). Резюме — простой текст без разметки.
    summary = onboarding.build_summary(
        {
            "niche": niche,
            "audience": audience,
            "tone": tone_str,
            "accounts": refs,
        }
    )
    await message.answer(summary, parse_mode=None)
    await message.answer(MENU_PROMPT, reply_markup=main_menu_keyboard())


# ───────────────────── Пункты главного меню ─────────────────────
#
# Все пункты «menu:*» теперь обрабатываются в других роутерах (регистрируются в
# main.py ПОСЛЕ start.router):
#   - menu:post / menu:reel / menu (🏠 В меню) → handlers/callbacks.py (Фазы 6/7);
#   - menu:drafts / menu:profile              → handlers/menu.py (Фаза 8).
# Поэтому здесь заглушек больше нет.
