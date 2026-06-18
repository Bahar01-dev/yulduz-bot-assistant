"""Сборка системного промпта контент-агента из профиля бренда (spec.md §6.2, §13.1).

Принцип (§13.1): профиль бренда передаётся в КАЖДЫЙ системный промпт целиком,
история последних 10-20 тем — для дедупликации. Формат вывода задаётся жёстко
и приходит снаружи через {format_instructions} (из post_prompt / reel_prompt).

Слои не смешиваем: загрузку profile+recent_topics из БД делает вызывающий код
(Фазы 6/7), сюда они приходят уже готовыми. Поэтому функция СИНХРОННАЯ.
"""

# ── Шаблон системного промпта (дословно по spec.md §6.2) ──────────────────
# {format_instructions} — точка подстановки конкретных инструкций формата
# (хуки / полный пост / сценарий Reels). Их даёт post_prompt / reel_prompt.
_SYSTEM_PROMPT_TEMPLATE = """\
Ты — персональный контент-менеджер и копирайтер.

ПРОФИЛЬ БРЕНДА:
Ниша: {niche}
Аудитория: {target_audience}
Тон: {tone}
Запрещённые слова/фразы: {forbidden_words}
Стиль-ориентир: {style_description}

ИСТОРИЯ: Уже написаны посты на темы: {recent_topics_list}
Избегай повторения этих тем и формулировок.

ПРАВИЛА ГЕНЕРАЦИИ:
- Пиши как живой эксперт, не как ChatGPT
- Никакого "В заключение", "Таким образом", "Безусловно"
- Конкретные цифры и примеры вместо общих слов
- Разговорный стиль, но с экспертной глубиной
- Хук — первые 1-2 строки — должны останавливать скролл

{format_instructions}"""

# Текст-заглушки для пустых/None полей, чтобы промпт оставался осмысленным.
_EMPTY_FORBIDDEN = "(не заданы — особых запретов нет)"
_EMPTY_STYLE = "(не задан)"
_EMPTY_REFERENCES = "(не заданы)"
_EMPTY_TOPICS = "(история пуста — это первая публикация)"


def _clean(value, fallback: str) -> str:
    """Возвращает обрезанное строковое значение или fallback для пустого/None."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _format_topics(recent_topics) -> str:
    """Собирает список недавних тем в одну строку через '; '.

    Принимает list[str] | None. Пустые элементы отбрасываются.
    """
    if not recent_topics:
        return _EMPTY_TOPICS
    items = [str(t).strip() for t in recent_topics if t and str(t).strip()]
    if not items:
        return _EMPTY_TOPICS
    return "; ".join(items)


def build_system_prompt(
    profile: dict,
    recent_topics: list[str] | None = None,
    format_instructions: str = "",
) -> str:
    """Собирает полный системный промпт контент-агента.

    Args:
        profile: dict из brand_profile.get_by_user() с полями niche,
            target_audience, tone, forbidden_words, reference_accounts,
            style_description. Любое поле может быть пустым/None.
        recent_topics: список последних тем (10-20) для дедупликации.
        format_instructions: инструкции формата вывода (из post_prompt /
            reel_prompt). Подставляются в точку {format_instructions}.

    Returns:
        Готовый системный промпт (str) на русском.
    """
    profile = profile or {}

    # Референс-аккаунты в §6.2 явно не выводятся отдельной строкой, но это
    # часть стиль-ориентира — дописываем к style_description, если заданы.
    style_description = _clean(profile.get("style_description"), _EMPTY_STYLE)
    references = _clean(profile.get("reference_accounts"), "")
    if references:
        if style_description == _EMPTY_STYLE:
            style_description = f"ориентир на аккаунты: {references}"
        else:
            style_description = f"{style_description}; ориентир на аккаунты: {references}"

    return _SYSTEM_PROMPT_TEMPLATE.format(
        niche=_clean(profile.get("niche"), "(не задана)"),
        target_audience=_clean(profile.get("target_audience"), "(не задана)"),
        tone=_clean(profile.get("tone"), "(не задан)"),
        forbidden_words=_clean(profile.get("forbidden_words"), _EMPTY_FORBIDDEN),
        style_description=style_description,
        recent_topics_list=_format_topics(recent_topics),
        format_instructions=format_instructions,
    )
