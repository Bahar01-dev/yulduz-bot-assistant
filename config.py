"""Загрузка и валидация переменных окружения через python-dotenv.

Импорт config падает с понятной русской ошибкой, если не задан обязательный ключ
(TELEGRAM_BOT_TOKEN или OWNER_TELEGRAM_ID). GROQ_API_KEY опционален.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Подхватываем .env из текущей папки (если есть)
load_dotenv()


class ConfigError(Exception):
    """Ошибка конфигурации: отсутствует или некорректен обязательный ключ."""


def _require(name: str) -> str:
    """Возвращает обязательную переменную окружения или бросает понятную ошибку."""
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Не задан обязательный ключ окружения '{name}'. "
            f"Добавь его в .env (см. .env.example) или в переменные окружения Railway."
        )
    return value


def _require_int(name: str) -> int:
    """Возвращает обязательную целочисленную переменную окружения."""
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Ключ '{name}' должен быть целым числом (Telegram ID), получено: '{raw}'."
        ) from exc


@dataclass(frozen=True)
class Config:
    """Конфигурация бота, собранная из переменных окружения."""

    TELEGRAM_BOT_TOKEN: str
    ANTHROPIC_API_KEY: str
    GROQ_API_KEY: str | None
    TAVILY_API_KEY: str | None
    OWNER_TELEGRAM_ID: int
    DATABASE_PATH: str
    VOICE_ENABLED: bool
    TRENDS_ENABLED: bool
    # Трендовый бриф по расписанию (V2.1, §20): время «HH:MM» и IANA-пояс.
    TREND_DIGEST_TIME: str
    TREND_TZ: str
    # LLM-провайдер: если LLM_BASE_URL задан — используем OpenAI-совместимый
    # шлюз (например OpenRouter); иначе — прямой Anthropic API.
    LLM_BASE_URL: str | None
    LLM_MODEL: str


def _load_config() -> Config:
    """Собирает Config, валидируя обязательные ключи."""
    telegram_token = _require("TELEGRAM_BOT_TOKEN")
    owner_id = _require_int("OWNER_TELEGRAM_ID")

    # ANTHROPIC_API_KEY нужен для генерации (Фаза 1+); в Фазе 0 не валидируем жёстко
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    # GROQ опционален: включает голосовой ввод
    groq_key = os.getenv("GROQ_API_KEY") or None
    voice_enabled = bool(groq_key)

    # TAVILY опционален: включает анализ трендов/конкурентов (V2)
    tavily_key = os.getenv("TAVILY_API_KEY") or None
    trends_enabled = bool(tavily_key)

    # Трендовый бриф по расписанию (V2.1, §20). Дефолты: 09:00, Asia/Almaty.
    trend_digest_time = os.getenv("TREND_DIGEST_TIME") or "09:00"
    trend_tz = os.getenv("TREND_TZ") or "Asia/Almaty"

    # Путь к БД: по умолчанию локально data/bot.db
    database_path = os.getenv("DATABASE_PATH", "data/bot.db")

    # LLM-шлюз. LLM_BASE_URL задан → OpenAI-совместимый провайдер (OpenRouter);
    # пусто → прямой Anthropic API. LLM_MODEL — id модели у выбранного провайдера.
    llm_base_url = os.getenv("LLM_BASE_URL") or None
    # По умолчанию: прямой Anthropic — claude-opus-4-8; OpenRouter — слаг провайдера.
    default_model = "anthropic/claude-opus-4" if llm_base_url else "claude-opus-4-8"
    llm_model = os.getenv("LLM_MODEL") or default_model

    return Config(
        TELEGRAM_BOT_TOKEN=telegram_token,
        ANTHROPIC_API_KEY=anthropic_key,
        GROQ_API_KEY=groq_key,
        TAVILY_API_KEY=tavily_key,
        OWNER_TELEGRAM_ID=owner_id,
        DATABASE_PATH=database_path,
        VOICE_ENABLED=voice_enabled,
        TRENDS_ENABLED=trends_enabled,
        TREND_DIGEST_TIME=trend_digest_time,
        TREND_TZ=trend_tz,
        LLM_BASE_URL=llm_base_url,
        LLM_MODEL=llm_model,
    )


# Готовый экземпляр конфигурации (импортируется другими модулями)
config = _load_config()
