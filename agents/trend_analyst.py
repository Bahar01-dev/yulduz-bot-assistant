"""Агент анализа трендов/конкурентов (V2, spec.md §18 «Тренды», §17.1).

Два шага:
  ШАГ 1 — safe_search(query): веб-поиск Tavily по нише профиля → сырые результаты.
  ШАГ 2 — safe_generate(): Claude сворачивает результаты в разбор трендов + идеи
          под нишу/тон профиля (build_system_prompt + trend_instruction).

Оба внешних вызова идут через безопасные обёртки (safe_search/safe_generate):
любая ошибка превращается в GenerationResult с готовым RU-сообщением. Если
веб-поиск недоступен (нет ключа/SDK/сбой) — возвращаем его ошибку, не доходя
до LLM.
"""

from __future__ import annotations

from prompts.style_builder import build_system_prompt
from prompts.trend_prompt import (
    TREND_TEMPERATURE,
    build_search_query,
    trend_instruction,
)
from utils.errors import GenerationResult, safe_generate, safe_search
from utils.logger import get_logger

logger = get_logger(__name__)

_TREND_USER_COMMAND = "Сделай разбор трендов строго в заданном формате."


async def generate_trends(
    profile: dict,
    user_query: str = "",
    bot=None,
    temperature: float = TREND_TEMPERATURE,
) -> GenerationResult:
    """Собирает тренды по нише профиля и сворачивает их в разбор + идеи.

    ШАГ 1: формируем поисковый запрос и зовём safe_search. При ошибке поиска —
    сразу возвращаем её GenerationResult (RU-сообщение SEARCH_ERROR).
    ШАГ 2: подаём найденное в Claude (system = профиль + trend_instruction).
    Возвращает GenerationResult; при успехе .text форматирует format_trends().
    """
    query = build_search_query(profile, user_query)
    logger.info("generate_trends: запрос=%r", query)

    search = await safe_search(query=query)
    if not search.ok:
        # Поиск недоступен/сбоил — отдаём его дружелюбное RU-сообщение как есть.
        return search

    system = build_system_prompt(
        profile=profile,
        recent_topics=None,
        format_instructions=trend_instruction(search.text),
    )
    messages = [{"role": "user", "content": _TREND_USER_COMMAND}]
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=temperature,
        max_tokens=2048,
    )
