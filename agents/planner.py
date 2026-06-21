"""Агент контент-плана через Claude (V2, spec.md §18 «Контент-план»).

Один вызов LLM: system = профиль бренда + история тем (build_system_prompt) +
plan_instruction(period); user = короткая команда. При успехе в .text — план в
формате PLAN_MARKER + пронумерованные строки, который форматирует format_plan().

Все вызовы идут через safe_generate() (retry/backoff + маппинг ошибок).
"""

from __future__ import annotations

from prompts.plan_prompt import DEFAULT_PERIOD, PLAN_TEMPERATURE, plan_instruction
from prompts.style_builder import build_system_prompt
from utils.errors import GenerationResult, safe_generate
from utils.logger import get_logger

logger = get_logger(__name__)

# Пользовательское сообщение: период и формат уже лежат в system-промпте.
_PLAN_USER_COMMAND = "Составь контент-план строго в заданном формате."


async def generate_plan(
    profile: dict,
    recent_topics: list[str],
    period: str = DEFAULT_PERIOD,
    bot=None,
    temperature: float = PLAN_TEMPERATURE,
    style_examples: dict | None = None,
) -> GenerationResult:
    """Генерирует контент-план на период (week/month).

    system = профиль + история тем (для не-повтора, §13.2) + примеры стиля
    (§13.3) + plan_instruction(period). Возвращает GenerationResult; при успехе
    в .text — сырой план (PLAN_MARKER + строки), парсится/форматится format_plan().
    """
    system = build_system_prompt(
        profile=profile,
        recent_topics=recent_topics,
        format_instructions=plan_instruction(period),
        style_examples=style_examples,
    )
    messages = [{"role": "user", "content": _PLAN_USER_COMMAND}]
    logger.info("generate_plan: период=%s, t=%.2f", period, temperature)
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=temperature,
        max_tokens=2048,
    )
