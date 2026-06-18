"""Сквозной слой обработки ошибок LLM/API и логирования (Фаза 2).

Принципы (см. Plan.md, раздел «Обработка ошибок LLM»):
  №1 — пользователь НИКОГДА не видит технический текст, стектрейс, английское
       сообщение об ошибке или HTTP-код. Только понятное русское сообщение +
       подсказку, что делать дальше.
  №2 — разработчик ВСЕГДА получает полную техническую информацию (тип,
       сообщение, traceback) в stdout (Railway logs); при критических сбоях —
       ещё и короткий алерт владельцу в Telegram.

Состав модуля:
  - ERROR_CATALOG  — каталог ситуаций → (RU-сообщение, уровень лога, алерт, retry).
  - get_anthropic_client() — фабрика AsyncAnthropic с ключом из config.
  - safe_generate(...)     — обёртка вызова Claude: retry+backoff, маппинг ошибок,
                             возвращает GenerationResult (успех/ошибка).
  - alert_owner(bot, text) — троттлящееся уведомление владельцу.

ВАЖНО: пакет ``anthropic`` может быть не установлен в окружении (например, при
проверке py_compile). Импорт защищён try/except, чтобы модуль грузился всегда;
классы исключений ловятся по иерархии и через getattr, что устойчиво к мелким
расхождениям имён между версиями SDK 0.28.x.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from config import config
from utils.logger import get_logger, log_exception

logger = get_logger(__name__)

# Модель LLM: id у выбранного провайдера (Anthropic — claude-opus-4-8;
# OpenRouter — anthropic/claude-opus-4). Берётся из config (env LLM_MODEL).
MODEL = config.LLM_MODEL

# ---------------------------------------------------------------------------
# Защищённый импорт anthropic SDK
# ---------------------------------------------------------------------------
# Если пакет не установлен, _ANTHROPIC_AVAILABLE=False; модуль всё равно грузится,
# а safe_generate вернёт корректную ситуацию unknown_error при попытке вызова.
try:
    import anthropic  # type: ignore
    from anthropic import AsyncAnthropic  # type: ignore

    _ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    anthropic = None  # type: ignore
    AsyncAnthropic = None  # type: ignore
    _ANTHROPIC_AVAILABLE = False

# OpenAI-совместимый SDK — для шлюзов вроде OpenRouter (config.LLM_BASE_URL).
try:
    import openai  # type: ignore
    from openai import AsyncOpenAI  # type: ignore

    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    openai = None  # type: ignore
    AsyncOpenAI = None  # type: ignore
    _OPENAI_AVAILABLE = False

# Используем OpenAI-совместимый путь, если задан базовый URL (OpenRouter и т.п.).
_USE_OPENAI = bool(config.LLM_BASE_URL)

# Groq SDK — для голосовой расшифровки (Whisper). Импорт защищён так же, как
# anthropic/openai: если пакета нет, _GROQ_AVAILABLE=False, а safe_transcribe
# вернёт ситуацию GROQ_ERROR при попытке вызова.
try:
    import groq  # type: ignore
    from groq import AsyncGroq  # type: ignore

    _GROQ_AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    groq = None  # type: ignore
    AsyncGroq = None  # type: ignore
    _GROQ_AVAILABLE = False

# Модель распознавания речи Groq Whisper. whisper-large-v3 — мультиязычная,
# лучшее качество для русского. Переопределяется env WHISPER_MODEL при желании.
WHISPER_MODEL = os.getenv("WHISPER_MODEL") or "whisper-large-v3"


def _exc(name: str) -> tuple[type[BaseException], ...]:
    """Классы исключений с данным именем из обоих SDK (anthropic и openai).

    У anthropic и openai иерархии исключений совпадают по именам
    (APIConnectionError, RateLimitError, AuthenticationError…). Собираем классы
    из всех доступных пакетов и возвращаем кортеж — его принимает isinstance().
    Если ни одного нет — недостижимая заглушка, которую ``except`` не поймает.
    """
    classes: list[type[BaseException]] = []
    for mod in (anthropic, openai):
        if mod is not None:
            cls = getattr(mod, name, None)
            if (
                isinstance(cls, type)
                and issubclass(cls, BaseException)
                and cls not in classes
            ):
                classes.append(cls)
    if not classes:
        return (type(f"_Missing_{name}", (Exception,), {}),)
    return tuple(classes)


# Классы исключений из доступных SDK (через защищённый доступ).
# Иерархия (общая для anthropic/openai): APIError -> APIStatusError ->
# {BadRequestError(400), AuthenticationError(401), PermissionDeniedError(403),
# RateLimitError(429), InternalServerError(5xx)}; APIConnectionError(сеть) ->
# APITimeoutError(таймаут).
APIError = _exc("APIError")
APIConnectionError = _exc("APIConnectionError")
APITimeoutError = _exc("APITimeoutError")
APIStatusError = _exc("APIStatusError")
RateLimitError = _exc("RateLimitError")
AuthenticationError = _exc("AuthenticationError")
PermissionDeniedError = _exc("PermissionDeniedError")
BadRequestError = _exc("BadRequestError")
InternalServerError = _exc("InternalServerError")


# ---------------------------------------------------------------------------
# Ситуации каталога ошибок
# ---------------------------------------------------------------------------
class Situation(str, Enum):
    """Ключи ситуаций из таблицы «Каталог ошибок» (Plan.md)."""

    NO_CONNECTION = "no_connection"          # Нет связи с Anthropic (сеть)
    RATE_LIMIT = "rate_limit"                # Лимит запросов (429)
    SERVER_ERROR = "server_error"            # Сбой сервера ИИ (5xx)
    AUTH_ERROR = "auth_error"                # Неверный/отсутствует ключ (401)
    BAD_REQUEST = "bad_request"              # Слишком большой запрос (400)
    TIMEOUT = "timeout"                      # Таймаут генерации
    EMPTY_RESPONSE = "empty_response"        # Пустой/нечитаемый ответ модели
    CONTENT_FILTER = "content_filter"        # Модель отказалась / контент-фильтр
    GROQ_ERROR = "groq_error"                # Ошибка Groq Whisper (голос)
    DB_ERROR = "db_error"                    # Ошибка записи в БД
    UNKNOWN = "unknown_error"                # Любая прочая ошибка (catch-all)


@dataclass(frozen=True)
class ErrorPolicy:
    """Политика обработки одной ситуации из каталога.

    Атрибуты:
        user_message: готовое RU-сообщение для пользователя (дословно из Plan.md).
        log_level:    уровень логирования (logging.WARNING/ERROR/CRITICAL).
        alert_owner:  слать ли короткий алерт владельцу в Telegram.
        retries:      число ДОПОЛНИТЕЛЬНЫХ повторов вызова (0 = без retry).
    """

    user_message: str
    log_level: int
    alert_owner: bool
    retries: int


# ---------------------------------------------------------------------------
# КАТАЛОГ ОШИБОК — тексты ДОСЛОВНО из таблицы «Каталог ошибок» в Plan.md.
# Колонки таблицы: RU-сообщение | уровень лога | алерт владельцу | retry.
# ---------------------------------------------------------------------------
ERROR_CATALOG: dict[Situation, ErrorPolicy] = {
    # Нет связи с Anthropic (APIConnectionError, сеть) | ERROR+traceback | да | ×2
    Situation.NO_CONNECTION: ErrorPolicy(
        user_message="Сервис временно недоступен. Попробуй ещё раз через минуту 🙏",
        log_level=logging.ERROR,
        alert_owner=True,
        retries=2,
    ),
    # Лимит запросов (RateLimitError, 429) | WARNING | нет | нет
    Situation.RATE_LIMIT: ErrorPolicy(
        user_message="Слишком много запросов подряд. Подожди минутку и попробуй снова.",
        log_level=logging.WARNING,
        alert_owner=False,
        retries=0,
    ),
    # Сбой сервера ИИ (APIStatusError 5xx) | ERROR | да | ×2
    Situation.SERVER_ERROR: ErrorPolicy(
        user_message="Сервис ИИ временно недоступен. Попробуй чуть позже.",
        log_level=logging.ERROR,
        alert_owner=True,
        retries=2,
    ),
    # Неверный/отсутствует ключ (AuthenticationError, 401) | CRITICAL | да | нет
    Situation.AUTH_ERROR: ErrorPolicy(
        user_message="Бот настроен некорректно. Я уже сообщил владельцу — скоро починим.",
        log_level=logging.CRITICAL,
        alert_owner=True,
        retries=0,
    ),
    # Слишком большой запрос (BadRequestError, 400, контекст) | ERROR | нет | нет
    Situation.BAD_REQUEST: ErrorPolicy(
        user_message="Запрос получился слишком большим. Давай короче — переформулируй, пожалуйста.",
        log_level=logging.ERROR,
        alert_owner=False,
        retries=0,
    ),
    # Таймаут генерации | WARNING | нет | ×1
    Situation.TIMEOUT: ErrorPolicy(
        user_message="Генерация затянулась. Попробуй ещё раз — обычно помогает.",
        log_level=logging.WARNING,
        alert_owner=False,
        retries=1,
    ),
    # Пустой/нечитаемый ответ модели | ERROR | нет | ×1
    Situation.EMPTY_RESPONSE: ErrorPolicy(
        user_message="Не получилось собрать ответ. Нажми «🔄 Переписать» — попробую заново.",
        log_level=logging.ERROR,
        alert_owner=False,
        retries=1,
    ),
    # Модель отказалась / контент-фильтр | WARNING | нет | нет
    Situation.CONTENT_FILTER: ErrorPolicy(
        user_message="ИИ не смог обработать эту тему. Попробуй переформулировать.",
        log_level=logging.WARNING,
        alert_owner=False,
        retries=0,
    ),
    # Ошибка Groq Whisper (голос) | WARNING | нет | ×1
    Situation.GROQ_ERROR: ErrorPolicy(
        user_message="Голосовой ввод временно недоступен. Напиши текстом, пожалуйста 🙏",
        log_level=logging.WARNING,
        alert_owner=False,
        retries=1,
    ),
    # Ошибка записи в БД | ERROR | да | ×1
    Situation.DB_ERROR: ErrorPolicy(
        user_message="Не получилось сохранить. Попробуй ещё раз через секунду.",
        log_level=logging.ERROR,
        alert_owner=True,
        retries=1,
    ),
    # Любая прочая ошибка (catch-all) | ERROR+traceback | да | нет
    Situation.UNKNOWN: ErrorPolicy(
        user_message="Что-то пошло не так. Я уже разбираюсь — попробуй ещё раз чуть позже.",
        log_level=logging.ERROR,
        alert_owner=True,
        retries=0,
    ),
}


def get_policy(situation: Situation) -> ErrorPolicy:
    """Возвращает политику для ситуации (на catch-all падает в UNKNOWN)."""
    return ERROR_CATALOG.get(situation, ERROR_CATALOG[Situation.UNKNOWN])


# ---------------------------------------------------------------------------
# Результат генерации
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GenerationResult:
    """Результат вызова safe_generate — чётко различает успех и ошибку.

    Поля:
        ok:           True — успех (см. text), False — ошибка (см. user_message).
        text:         текст ответа модели при успехе, иначе None.
        user_message: готовое RU-сообщение для пользователя при ошибке, иначе None.
        situation:    ключ ситуации каталога при ошибке (str), иначе None.
        alert_owner:  нужно ли слать алерт владельцу (актуально при ошибке).
    """

    ok: bool
    text: str | None = None
    user_message: str | None = None
    situation: str | None = None
    alert_owner: bool = False

    @classmethod
    def success(cls, text: str) -> "GenerationResult":
        """Фабрика успешного результата."""
        return cls(ok=True, text=text)

    @classmethod
    def failure(cls, situation: Situation) -> "GenerationResult":
        """Фабрика результата-ошибки на основе политики каталога."""
        policy = get_policy(situation)
        return cls(
            ok=False,
            text=None,
            user_message=policy.user_message,
            situation=situation.value,
            alert_owner=policy.alert_owner,
        )


# ---------------------------------------------------------------------------
# Фабрика клиента Anthropic
# ---------------------------------------------------------------------------
def get_llm_client():
    """Создаёт LLM-клиент по config: OpenAI-совместимый (OpenRouter) или Anthropic.

    Если config.LLM_BASE_URL задан — AsyncOpenAI с этим base_url (OpenRouter);
    иначе — прямой AsyncAnthropic. Ключ берётся из config.ANTHROPIC_API_KEY.
    Бросает RuntimeError, если нужный SDK не установлен или ключ пуст — вызывающий
    код (safe_generate) перехватывает это и мапит в ситуацию AUTH/UNKNOWN.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("API-ключ LLM не задан.")
    if _USE_OPENAI:
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("Пакет 'openai' не установлен.")
        return AsyncOpenAI(
            api_key=config.ANTHROPIC_API_KEY,
            base_url=config.LLM_BASE_URL,
        )
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError("Пакет 'anthropic' не установлен.")
    return AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


# Обратная совместимость: старое имя фабрики (если где-то используется).
get_anthropic_client = get_llm_client


# ---------------------------------------------------------------------------
# Маппинг исключения → ситуация каталога
# ---------------------------------------------------------------------------
def classify_exception(exc: BaseException) -> Situation:
    """Сопоставляет исключение Anthropic с ситуацией каталога.

    Порядок проверок важен: специфичные классы (RateLimit/Auth/BadRequest)
    проверяются ДО общего APIStatusError, а APITimeoutError — до его базы
    APIConnectionError.
    """
    # Таймаут (наследует APIConnectionError, поэтому проверяем первым)
    if isinstance(exc, APITimeoutError):
        return Situation.TIMEOUT
    # Нет связи / сетевые ошибки
    if isinstance(exc, APIConnectionError):
        return Situation.NO_CONNECTION
    # Лимит запросов (429)
    if isinstance(exc, RateLimitError):
        return Situation.RATE_LIMIT
    # Неверный/отсутствует ключ (401)
    if isinstance(exc, AuthenticationError):
        return Situation.AUTH_ERROR
    # Слишком большой/некорректный запрос (400)
    if isinstance(exc, BadRequestError):
        return Situation.BAD_REQUEST
    # Прочие HTTP-статусы: 5xx → server_error; остальные 4xx → unknown
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return Situation.SERVER_ERROR
        if isinstance(exc, InternalServerError):
            return Situation.SERVER_ERROR
        return Situation.UNKNOWN
    # Любой прочий APIError без статуса — считаем сбоем сервера
    if isinstance(exc, APIError):
        return Situation.SERVER_ERROR
    # Всё остальное — catch-all
    return Situation.UNKNOWN


# Ситуации, для которых retry имеет смысл (сетевые/5xx/таймаут/пустой ответ).
_RETRYABLE = {
    Situation.NO_CONNECTION,
    Situation.SERVER_ERROR,
    Situation.TIMEOUT,
    Situation.EMPTY_RESPONSE,
}

# Базовые задержки backoff между попытками (≈1с, 2с) — Plan.md.
_BACKOFF_DELAYS = [1.0, 2.0]


def _extract_text(response: Any) -> str | None:
    """Достаёт текст из ответа LLM (OpenAI-совместимый ИЛИ Anthropic).

    OpenAI/OpenRouter: response.choices[0].message.content (строка).
    Anthropic: response.content — список блоков, берём блоки type == "text".
    Возвращает None, если текста нет/ответ нечитаем — это трактуется как
    ситуация EMPTY_RESPONSE.
    """
    try:
        # OpenAI-совместимый ответ (chat.completions)
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            text = getattr(message, "content", None) if message is not None else None
            if isinstance(text, str) and text.strip():
                return text.strip()
            return None

        # Anthropic ответ (messages.create): .content — список блоков
        content = getattr(response, "content", None)
        if not content:
            return None
        parts: list[str] = []
        for block in content:
            # Блок может быть объектом (с .type/.text) или dict
            btype = getattr(block, "type", None)
            if btype is None and isinstance(block, dict):
                btype = block.get("type")
            if btype == "text":
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if text:
                    parts.append(text)
        result = "".join(parts).strip()
        return result or None
    except Exception:  # нечитаемая структура ответа
        return None


async def safe_generate(
    *,
    messages: list[dict[str, Any]],
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    client: "AsyncAnthropic | None" = None,
    model: str = MODEL,
) -> GenerationResult:
    """Безопасная обёртка вызова Claude (Anthropic Messages API).

    Поведение:
      - Вызывает client.messages.create(...) с гибкими параметрами.
      - retry с экспоненциальным backoff (≈1с, 2с) ТОЛЬКО для ситуаций из
        каталога с retry ×1/×2: сетевые, 5xx, таймаут, пустой ответ.
        Для RateLimit/Auth/BadRequest/контент-фильтра — без retry.
      - Каждый тип исключения Anthropic мапится на ситуацию каталога,
        логируется на нужном уровне (с traceback на ERROR/CRITICAL).
      - Пустой/нечитаемый ответ → ситуация EMPTY_RESPONSE (с retry ×1).

    Аргументы:
        messages:    список сообщений Anthropic [{"role": "user", "content": ...}].
        system:      системный промпт (опционально).
        temperature: температура (0.7 пост / 0.8 reels) — задаётся снаружи.
        max_tokens:  лимит токенов ответа.
        client:      готовый AsyncAnthropic (для переиспользования/тестов); если
                     None — создаётся через get_anthropic_client().
        model:       идентификатор модели (по умолчанию claude-opus-4-8).

    Возвращает:
        GenerationResult — ok=True с .text при успехе, иначе ok=False с готовым
        RU-сообщением (.user_message), ключом ситуации (.situation) и флагом
        .alert_owner.
    """
    # Готовим клиент (ошибку создания мапим в ситуацию ниже)
    try:
        active_client = client or get_llm_client()
    except Exception as exc:  # нет SDK / нет ключа
        # Нет ключа → ведём себя как AUTH; иначе → UNKNOWN (catch-all)
        situation = Situation.AUTH_ERROR if not config.ANTHROPIC_API_KEY else Situation.UNKNOWN
        _log_situation(situation, exc)
        return GenerationResult.failure(situation)

    # Параметры вызова зависят от SDK. OpenAI-совместимый (OpenRouter): system —
    # это отдельное сообщение с role=system. Anthropic: system — отдельный параметр.
    if _USE_OPENAI:
        oa_messages = list(messages)
        if system is not None:
            oa_messages = [{"role": "system", "content": system}] + oa_messages
        params: dict[str, Any] = {
            "model": model,
            "messages": oa_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    else:
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system is not None:
            params["system"] = system

    attempt = 0
    while True:
        try:
            if _USE_OPENAI:
                response = await active_client.chat.completions.create(**params)
            else:
                response = await active_client.messages.create(**params)
            text = _extract_text(response)
            if text is None:
                # Пустой/нечитаемый ответ — ретраим как EMPTY_RESPONSE
                situation = Situation.EMPTY_RESPONSE
                logger.log(
                    get_policy(situation).log_level,
                    "Пустой/нечитаемый ответ модели (попытка %d).",
                    attempt + 1,
                )
                if _should_retry(situation, attempt):
                    await _sleep_backoff(attempt)
                    attempt += 1
                    continue
                return GenerationResult.failure(situation)
            # Успех
            return GenerationResult.success(text)

        except Exception as exc:  # ловим всё, классифицируем по каталогу
            situation = classify_exception(exc)
            _log_situation(situation, exc, attempt=attempt)
            if _should_retry(situation, attempt):
                await _sleep_backoff(attempt)
                attempt += 1
                continue
            return GenerationResult.failure(situation)


def _should_retry(situation: Situation, attempt: int) -> bool:
    """True, если ситуация ретраибельна и не исчерпан лимит повторов каталога."""
    if situation not in _RETRYABLE:
        return False
    return attempt < get_policy(situation).retries


async def _sleep_backoff(attempt: int) -> None:
    """Экспоненциальная задержка перед повтором (≈1с, затем 2с)."""
    delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]
    await asyncio.sleep(delay)


def _log_situation(
    situation: Situation,
    exc: BaseException,
    *,
    attempt: int | None = None,
) -> None:
    """Логирует ситуацию на уровне из каталога; на ERROR/CRITICAL — с traceback."""
    policy = get_policy(situation)
    suffix = f" (попытка {attempt + 1})" if attempt is not None else ""
    message = (
        f"Ошибка генерации [{situation.value}]{suffix}: "
        f"{type(exc).__name__}: {exc}"
    )
    if policy.log_level >= logging.ERROR:
        # ERROR и CRITICAL — с полным traceback для разработчика
        log_exception(logger, message, level=policy.log_level)
    else:
        logger.log(policy.log_level, message)


# ---------------------------------------------------------------------------
# Алерт владельцу с троттлингом/дедупом
# ---------------------------------------------------------------------------
# Минимальный интервал между ОДИНАКОВЫМИ алертами (секунды), чтобы не спамить.
_ALERT_THROTTLE_SECONDS = 60.0
# Хранилище последней отправки по тексту: {текст: timestamp}
_last_alert_sent: dict[str, float] = {}


async def alert_owner(bot: Any, text: str) -> None:
    """Короткое уведомление владельцу (config.OWNER_TELEGRAM_ID) с троттлингом.

    - Дедуп: одинаковый текст не отправляется чаще раза в N секунд
      (_ALERT_THROTTLE_SECONDS). Последняя отправка хранится в модуле.
    - Сам ловит любые ошибки отправки и НЕ падает (алерт не должен ронять бота).
    """
    now = time.monotonic()
    last = _last_alert_sent.get(text)
    if last is not None and (now - last) < _ALERT_THROTTLE_SECONDS:
        # Тот же текст уже отправляли недавно — пропускаем (троттлинг)
        logger.debug("Алерт владельцу подавлен троттлингом: %s", text)
        return

    try:
        await bot.send_message(
            chat_id=config.OWNER_TELEGRAM_ID,
            text=f"⚠️ {text}",
        )
        _last_alert_sent[text] = now
    except Exception:  # отправка алерта не должна ронять бота
        log_exception(logger, "Не удалось отправить алерт владельцу.")


# ---------------------------------------------------------------------------
# Голосовая расшифровка через Groq Whisper (Фаза 9)
# ---------------------------------------------------------------------------
async def safe_transcribe(
    *,
    audio: bytes,
    filename: str = "voice.ogg",
    language: str = "ru",
    model: str = WHISPER_MODEL,
) -> GenerationResult:
    """Безопасная обёртка распознавания речи (Groq Whisper).

    Зеркалит логику safe_generate: ловит ЛЮБУЮ ошибку, логирует детали в stdout,
    при необходимости ретраит и возвращает GenerationResult — ok=True с .text
    (распознанный текст) при успехе, иначе ok=False с готовым RU-сообщением
    ситуации GROQ_ERROR (пользователь видит «Напиши текстом…», не тех.текст).

    Аргументы:
        audio:    байты аудио (.ogg от Telegram).
        filename: имя файла для SDK (расширение важно Groq для определения формата).
        language: язык распознавания (ISO-639-1), по умолчанию русский.
        model:    модель Whisper (по умолчанию whisper-large-v3).

    Ретраи: по политике каталога для GROQ_ERROR (×1). Пустой/нечитаемый результат
    трактуется как GROQ_ERROR (просим написать текстом).
    """
    situation = Situation.GROQ_ERROR
    retries = get_policy(situation).retries

    # Ключ/SDK отсутствует → сразу дружелюбное RU-сообщение (без тех.текста)
    if not config.GROQ_API_KEY or not _GROQ_AVAILABLE:
        logger.warning(
            "Голосовая расшифровка недоступна: GROQ_API_KEY=%s, SDK=%s",
            bool(config.GROQ_API_KEY),
            _GROQ_AVAILABLE,
        )
        return GenerationResult.failure(situation)

    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    attempt = 0
    while True:
        try:
            response = await client.audio.transcriptions.create(
                file=(filename, audio),
                model=model,
                language=language,
                response_format="text",
            )
            # response_format="text" → строка; на других форматах — объект с .text
            text = response if isinstance(response, str) else getattr(response, "text", None)
            if not text or not text.strip():
                logger.warning("Пустой результат расшифровки (попытка %d).", attempt + 1)
                if attempt < retries:
                    await _sleep_backoff(attempt)
                    attempt += 1
                    continue
                return GenerationResult.failure(situation)
            return GenerationResult.success(text.strip())

        except Exception as exc:  # любая ошибка Groq → GROQ_ERROR (+ ретрай по политике)
            _log_situation(situation, exc, attempt=attempt)
            if attempt < retries:
                await _sleep_backoff(attempt)
                attempt += 1
                continue
            return GenerationResult.failure(situation)
