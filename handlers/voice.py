"""Хэндлер голосовых сообщений (Groq Whisper STT, Фаза 9, spec.md §7.4).

Поток:
  голосовое (.ogg) → скачать → safe_transcribe (Groq Whisper) →
    «🎙 Услышал: «…»» → дальше как обычный текст (route_text) или тема потока.

Где можно говорить голосом: вне активного диалога (StateFilter None — свободный
запрос) и когда бот ждёт тему поста/Reels (PostFlow/ReelFlow.waiting_topic).
В онбординге и при выборе хука/редактировании профиля голос не перехватываем —
просим продолжить текстом/кнопками, чтобы не сломать пошаговый диалог.

Если GROQ_API_KEY не задан или Groq недоступен — safe_transcribe вернёт готовое
RU-сообщение «Голосовой ввод временно недоступен. Напиши текстом…» (без тех.текста).

Роутер регистрируется в main.py (фильтр F.voice не пересекается с текстовыми).
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.callbacks import send_typing
from handlers.message import (
    ASK_POST_TOPIC,
    ASK_REEL_TOPIC,
    handle_post_request,
    handle_reel_request,
    route_text,
)
from utils.errors import GenerationResult, Situation, alert_owner, safe_transcribe
from utils.logger import get_logger
from utils.state import PostFlow, ReelFlow

logger = get_logger(__name__)

router = Router(name="voice")

# Голос в пошаговых диалогах (онбординг/выбор хука/редактирование) не перехватываем.
_GUIDE_TEXT = (
    "Голосом я понимаю запросы на пост или Reels. "
    "Сейчас, пожалуйста, продолжи текстом или кнопками 🙏"
)

# Пустая/нечитаемая расшифровка (Whisper вернул успех, но без слов).
_EMPTY_TRANSCRIPT = "Не расслышал 🙏 Попробуй надиктовать ещё раз или напиши текстом."


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext) -> None:
    """Принимает голосовое, расшифровывает и направляет в нужный поток."""
    current = await state.get_state()

    # Голос обрабатываем только вне пошаговых диалогов: свободный запрос (None)
    # или ожидание темы поста/Reels.
    allowed = {None, PostFlow.waiting_topic.state, ReelFlow.waiting_topic.state}
    if current not in allowed:
        await message.answer(_GUIDE_TEXT)
        return

    # Скачиваем .ogg в память (BytesIO) и расшифровываем через Groq Whisper.
    await send_typing(message)
    try:
        buffer = await message.bot.download(message.voice)
        audio_bytes = buffer.read()
    except Exception:  # сбой скачивания файла → ведём себя как ошибку голоса
        logger.exception("Не удалось скачать голосовое сообщение")
        await message.answer(GenerationResult.failure(Situation.GROQ_ERROR).user_message)
        return

    result = await safe_transcribe(audio=audio_bytes)
    if not result.ok:
        await message.answer(result.user_message)
        if result.alert_owner:
            await alert_owner(message.bot, f"Сбой голосовой расшифровки [{result.situation}].")
        return

    text = (result.text or "").strip()
    if not text:
        await message.answer(_EMPTY_TRANSCRIPT)
        return

    logger.info("Голос расшифрован: %r (state=%s)", text, current)
    # Подтверждение пользователю (spec.md §7.4). parse_mode=None — текст «как есть».
    await message.answer(f"🎙 Услышал: «{text}» ✓", parse_mode=None)

    # Дальше — как обычный текст. Если ждали тему поста/Reels — это тема целиком;
    # иначе (state None) — прогоняем через оркестратор (detect_intent).
    if current == PostFlow.waiting_topic.state:
        await handle_post_request(message, text, state)
        return

    if current == ReelFlow.waiting_topic.state:
        await handle_reel_request(message, text, state)
        return

    await route_text(message, text, state)
