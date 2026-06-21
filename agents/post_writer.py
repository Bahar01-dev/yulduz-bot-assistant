"""Агент написания постов Instagram через Claude (Фаза 6, spec.md §6.2, §7.2).

Двухшаговая генерация (spec.md §7.2):
  ШАГ 1 — generate_hooks():  один вызов LLM → РОВНО 3 хука (HOOK_1/2/3).
  ШАГ 2 — generate_post():   один вызов LLM по выбранному хуку → полный пост
          с маркерами (POST:/CTA:/ХЭШТЕГИ:/ЧЕКЛИСТ:).

Чистое разделение слоёв:
  - системный промпт = профиль бренда + история тем (build_system_prompt) +
    инструкция формата (hooks_instruction / post_format_instruction);
  - пользовательское сообщение = только тема (ШАГ 1) либо короткая команда
    «напиши пост» (ШАГ 2, т.к. хук и формат уже в system).

Все вызовы LLM идут через safe_generate() — он сам делает retry/backoff и
маппит ошибки в GenerationResult (ok/text/user_message/situation/alert_owner).
Здесь мы лишь собираем промпты и парсим сырой ответ хуков.
"""

from __future__ import annotations

from prompts.post_prompt import (
    HOOK_PREFIX,
    POST_TEMPERATURE,
    hooks_instruction,
    post_format_instruction,
)
from prompts.style_builder import build_system_prompt
from utils.errors import GenerationResult, safe_generate
from utils.logger import get_logger

logger = get_logger(__name__)

# Пользовательское сообщение ШАГА 2: хук и формат уже лежат в system-промпте,
# поэтому в user кладём короткую нейтральную команду.
_POST_USER_COMMAND = "Напиши полный пост по выбранному хуку строго в заданном формате."


async def generate_hooks(
    profile: dict,
    recent_topics: list[str],
    topic: str,
    bot=None,
    style_examples: dict | None = None,
) -> GenerationResult:
    """ШАГ 1: генерирует 3 варианта хука по теме.

    Собирает system = профиль + история + hooks_instruction(topic); тему кладёт
    в user-сообщение. Возвращает GenerationResult; при успехе в .text — сырой
    ответ с тремя строками HOOK_1/2/3 (парсится через parse_hooks()).

    Аргумент bot не используется внутри (alert владельцу шлёт вызывающий код по
    result.alert_owner), но оставлен в сигнатуре для единообразия потока.
    style_examples (V2, §13.3) — примеры одобренного/отклонённого стиля.
    """
    system = build_system_prompt(
        profile=profile,
        recent_topics=recent_topics,
        format_instructions=hooks_instruction(topic),
        style_examples=style_examples,
    )
    # Тема — в user-сообщении (профиль/формат — в system).
    messages = [{"role": "user", "content": topic}]
    logger.info("generate_hooks: тема=%r", topic)
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=POST_TEMPERATURE,
        max_tokens=1024,  # три хука — короткий ответ
    )


async def generate_post(
    profile: dict,
    recent_topics: list[str],
    topic: str,
    selected_hook: str,
    bot=None,
    temperature: float = POST_TEMPERATURE,
    style_examples: dict | None = None,
) -> GenerationResult:
    """ШАГ 2: генерирует полный пост по выбранному хуку.

    system = профиль + история + post_format_instruction(selected_hook);
    user = короткая команда написать пост. При успехе в .text — полный пост с
    маркерами (POST:/CTA:/ХЭШТЕГИ:/ЧЕКЛИСТ:), который форматирует format_post().

    temperature можно поднять для кнопки «💡 Другой вариант» (больше вариативности).
    style_examples (V2, §13.3) — примеры одобренного/отклонённого стиля.
    """
    system = build_system_prompt(
        profile=profile,
        recent_topics=recent_topics,
        format_instructions=post_format_instruction(selected_hook),
        style_examples=style_examples,
    )
    messages = [{"role": "user", "content": _POST_USER_COMMAND}]
    logger.info("generate_post: тема=%r, хук=%r, t=%.2f", topic, selected_hook, temperature)
    return await safe_generate(
        messages=messages,
        system=system,
        temperature=temperature,
        max_tokens=2048,
    )


def parse_hooks(raw: str) -> list[str]:
    """Извлекает варианты хуков из сырого ответа ШАГА 1.

    Ожидаемый формат — строки вида «HOOK_1: текст». Парсер устойчив:
      - игнорирует пустые/лишние строки и пробелы;
      - принимает регистр HOOK_/hook_;
      - снимает возможные обрамляющие кавычки/маркеры списка;
      - если строк HOOK_ не найдено — пытается взять пронумерованные строки
        «1.» / «1)» как запасной вариант.

    Возвращает список найденных хуков (обычно 3). Если меньше 3 — возвращает
    столько, сколько удалось распарсить (вызывающий код решает, что делать).
    """
    if not raw:
        return []

    hooks: list[str] = []
    prefix_lower = HOOK_PREFIX.lower()

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low.startswith(prefix_lower):
            # «HOOK_1: текст» → берём всё после первого двоеточия; если нет
            # двоеточия — отрезаем сам префикс с номером по первому пробелу.
            if ":" in stripped:
                value = stripped.split(":", 1)[1]
            else:
                value = stripped[len(HOOK_PREFIX):]
                # отрезаем ведущий номер (например «1 текст»)
                value = value.lstrip("0123456789").strip()
            value = _clean_hook(value)
            if value:
                hooks.append(value)

    # Fallback: если HOOK_-строк не нашлось, пробуем нумерованный список «1.»/«1)»
    if not hooks:
        for line in raw.splitlines():
            stripped = line.strip()
            if len(stripped) >= 2 and stripped[0].isdigit() and stripped[1] in ".)":
                value = _clean_hook(stripped[2:])
                if value:
                    hooks.append(value)

    return hooks


def _clean_hook(value: str) -> str:
    """Чистит текст хука: пробелы, обрамляющие кавычки и markdown-звёздочки."""
    value = value.strip()
    # Снимаем обрамляющие кавычки разных видов
    for quote in ('"', "«", "“", "”", "'"):
        if value.startswith(quote):
            value = value[1:]
        if value.endswith(quote) or value.endswith("»"):
            value = value[:-1]
    # Снимаем markdown-выделение по краям
    value = value.strip().strip("*").strip()
    return value
