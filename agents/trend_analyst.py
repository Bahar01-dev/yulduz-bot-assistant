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

from dataclasses import dataclass, field

from prompts.style_builder import build_system_prompt
from prompts.trend_prompt import (
    TREND_TEMPERATURE,
    brief_instruction,
    build_search_query,
    parse_brief_topics,
    trend_instruction,
)
from utils.errors import GenerationResult, safe_generate, safe_search
from utils.logger import get_logger

logger = get_logger(__name__)

_TREND_USER_COMMAND = "Сделай разбор трендов строго в заданном формате."
_BRIEF_USER_COMMAND = "Собери трендовый бриф строго в заданном формате."


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


# ─────────────────── Трендовый бриф по расписанию (V2.1, §20) ───────────────────


@dataclass(frozen=True)
class BriefResult:
    """Результат сборки трендового брифа (V2.1, §20).

    Поля:
        ok:           True — бриф собран (см. brief_text/topics/raw_search).
        brief_text:   сырой текст брифа (форматирует format_brief), иначе None.
        topics:       список тем брифа (для кнопок и последующего поста).
        raw_search:   сырые результаты Tavily = grounding для постов, иначе None.
        user_message: готовое RU-сообщение при ошибке, иначе None.
        situation:    ключ ситуации каталога при ошибке, иначе None.
        alert_owner:  нужно ли слать алерт владельцу (при ошибке).
    """

    ok: bool
    brief_text: str | None = None
    topics: list[str] = field(default_factory=list)
    raw_search: str | None = None
    user_message: str | None = None
    situation: str | None = None
    alert_owner: bool = False

    @classmethod
    def _from_failure(cls, result: GenerationResult) -> "BriefResult":
        """Переносит ошибку GenerationResult (safe_search/safe_generate) в BriefResult."""
        return cls(
            ok=False,
            user_message=result.user_message,
            situation=result.situation,
            alert_owner=result.alert_owner,
        )


async def fetch_trends(profile: dict, user_query: str = "") -> GenerationResult:
    """ШАГ 1 брифа: веб-поиск Tavily по нише профиля (safe_search) → raw_search.

    Отдельный «кирпичик»: результат используется и как grounding для постов, и
    как вход build_brief. При ошибке/недоступности поиска — его GenerationResult.
    """
    query = build_search_query(profile, user_query)
    logger.info("fetch_trends: запрос=%r", query)
    return await safe_search(query=query)


async def build_brief(
    raw_search: str,
    profile: dict,
    temperature: float = TREND_TEMPERATURE,
) -> GenerationResult:
    """ШАГ 2 брифа: Claude сворачивает raw_search в бриф (2-3 тренда + 3-5 тем).

    system = профиль + brief_instruction(raw_search); формат парсимый
    (BRIEF_MARKER + ТЕМА_N). Возвращает GenerationResult с сырым брифом в .text.
    """
    system = build_system_prompt(
        profile=profile,
        recent_topics=None,
        format_instructions=brief_instruction(raw_search),
    )
    messages = [{"role": "user", "content": _BRIEF_USER_COMMAND}]
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=temperature,
        max_tokens=1500,
    )


async def generate_brief(profile: dict) -> BriefResult:
    """Полный цикл трендового брифа: поиск → бриф → парсинг тем (V2.1, §20).

    Оркеструет fetch_trends + build_brief и парсит темы. При любой ошибке шага
    возвращает BriefResult(ok=False) с готовым RU-сообщением. Сохранение брифа в
    БД и отправку владельцу делает вызывающий слой (handlers/trends, scheduler).
    """
    search = await fetch_trends(profile)
    if not search.ok:
        return BriefResult._from_failure(search)

    brief = await build_brief(search.text, profile)
    if not brief.ok:
        return BriefResult._from_failure(brief)

    topics = parse_brief_topics(brief.text)
    logger.info("generate_brief: тем распознано=%d", len(topics))
    return BriefResult(
        ok=True,
        brief_text=brief.text,
        topics=topics,
        raw_search=search.text,
    )
