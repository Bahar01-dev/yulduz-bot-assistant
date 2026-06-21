"""Агент написания сценариев Reels через Claude (Фаза 7, spec.md §6.2, §7.3, §9.2).

Двухшаговая генерация — полностью зеркалит поток постов (Фаза 6):
  ШАГ 1 — generate_hooks(): один вызов LLM → РОВНО 3 хука (HOOK_1/2/3) для
          первых 3 секунд видео.
  ШАГ 2 — generate_reel():  один вызов LLM по выбранному хуку → побитовый
          сценарий с таймингами (метки 🎙/📺/🎬, разделители ━━━) под выбранную
          длину (15/30/60 сек), + ПОДПИСЬ и ХЭШТЕГИ.

Чистое разделение слоёв (как у post_writer):
  - системный промпт = профиль бренда + история тем (build_system_prompt) +
    инструкция формата (hooks_instruction / reel_format_instruction);
  - пользовательское сообщение = только тема (ШАГ 1) либо короткая команда
    (ШАГ 2, т.к. хук, длина и формат уже в system).

Все вызовы LLM идут через safe_generate() (retry/backoff + маппинг ошибок).
parse_hooks переиспользуется из post_writer — формат хуков (HOOK_N) общий.
"""

from __future__ import annotations

from agents.post_writer import parse_hooks  # noqa: F401  (общий парсер хуков, реэкспорт)
from prompts.reel_prompt import (
    DEFAULT_DURATION,
    REEL_TEMPERATURE,
    SUPPORTED_DURATIONS,
    hooks_instruction,
    reel_format_instruction,
)
from prompts.style_builder import build_system_prompt
from utils.errors import GenerationResult, safe_generate
from utils.logger import get_logger

logger = get_logger(__name__)

# Пользовательское сообщение ШАГА 2: хук, длина и формат уже в system-промпте.
_REEL_USER_COMMAND = "Напиши полный сценарий Reels по выбранному хуку строго в заданном формате."


async def generate_hooks(
    profile: dict,
    recent_topics: list[str],
    topic: str,
    bot=None,
    style_examples: dict | None = None,
) -> GenerationResult:
    """ШАГ 1: генерирует 3 варианта хука (первые 3 секунды) по теме Reels.

    Собирает system = профиль + история + hooks_instruction(topic); тему кладёт
    в user-сообщение. Возвращает GenerationResult; при успехе в .text — сырой
    ответ с тремя строками HOOK_1/2/3 (парсится через parse_hooks()).
    style_examples (V2, §13.3) — примеры одобренного/отклонённого стиля.
    """
    system = build_system_prompt(
        profile=profile,
        recent_topics=recent_topics,
        format_instructions=hooks_instruction(topic),
        style_examples=style_examples,
    )
    messages = [{"role": "user", "content": topic}]
    logger.info("reel.generate_hooks: тема=%r", topic)
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=REEL_TEMPERATURE,
        max_tokens=1024,  # три хука — короткий ответ
    )


async def generate_reel(
    profile: dict,
    recent_topics: list[str],
    topic: str,
    selected_hook: str,
    duration_sec: int = DEFAULT_DURATION,
    bot=None,
    temperature: float = REEL_TEMPERATURE,
    style_examples: dict | None = None,
) -> GenerationResult:
    """ШАГ 2: генерирует полный побитовый сценарий Reels по выбранному хуку.

    system = профиль + история + reel_format_instruction(selected_hook, duration);
    user = короткая команда. При успехе в .text — сценарий с маркерами
    (━━━ ХУК/БИТ/CTA ━━━, метки 🎙/📺/🎬, ПОДПИСЬ:/ХЭШТЕГИ:), который
    форматирует format_reel().

    duration_sec (15/30/60) задаёт число битов и тайминги; некорректное значение
    нормализуется внутри reel_format_instruction к DEFAULT_DURATION.
    style_examples (V2, §13.3) — примеры одобренного/отклонённого стиля.
    """
    if duration_sec not in SUPPORTED_DURATIONS:
        duration_sec = DEFAULT_DURATION
    system = build_system_prompt(
        profile=profile,
        recent_topics=recent_topics,
        format_instructions=reel_format_instruction(selected_hook, duration_sec),
        style_examples=style_examples,
    )
    messages = [{"role": "user", "content": _REEL_USER_COMMAND}]
    logger.info(
        "reel.generate_reel: тема=%r, хук=%r, длина=%dс, t=%.2f",
        topic,
        selected_hook,
        duration_sec,
        temperature,
    )
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=temperature,
        max_tokens=2048,
    )
