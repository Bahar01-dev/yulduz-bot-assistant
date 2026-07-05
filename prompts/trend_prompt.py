"""Инструкции для анализа трендов/конкурентов (V2, spec.md §18 «Тренды», §17.1).

Двухшаговый процесс (в agents/trend_analyst.py):
  ШАГ 1 — Tavily веб-поиск по нише профиля (safe_search) → сырые результаты.
  ШАГ 2 — LLM сворачивает результаты в разбор трендов + идеи под нишу/тон
          профиля (build_system_prompt + trend_instruction).

Маркер TREND_MARKER — для форматтера (utils.formatter.format_trends).
"""

# Temperature: ниже — нужен фактологичный разбор, а не фантазия.
TREND_TEMPERATURE = 0.6

TREND_MARKER = "РАЗБОР:"

# Трендовый бриф по расписанию (V2.1, §20): маркер тела брифа и префикс тем.
BRIEF_MARKER = "БРИФ:"
TOPIC_PREFIX = "ТЕМА_"  # ТЕМА_1 / ТЕМА_2 … — парсимо, по образцу HOOK_N


def build_search_query(profile: dict, user_query: str = "") -> str:
    """Собирает поисковый запрос для Tavily из ниши профиля (+ уточнения).

    Если пользователь задал конкретную тему (user_query) — она в приоритете;
    иначе ищем общие тренды по нише блога.
    """
    niche = (profile.get("niche") or "").strip()
    user_query = (user_query or "").strip()
    if user_query:
        base = user_query
    elif niche:
        base = niche
    else:
        base = "контент для Instagram"
    return f"актуальные тренды и популярные темы Instagram 2026: {base}"


def trend_instruction(search_results: str) -> str:
    """Инструкция: свернуть результаты веб-поиска в разбор трендов + идеи.

    search_results — свёрнутый текст из safe_search(). Жёсткий формат после
    TREND_MARKER, чтобы форматтер собрал аккуратный вывод.
    """
    return f"""\
ЗАДАЧА: на основе результатов веб-поиска ниже сделай краткий разбор актуальных
трендов для Instagram-блога в нише и тоне профиля и предложи идеи контента.

РЕЗУЛЬТАТЫ ПОИСКА:
{search_results}

Опирайся ТОЛЬКО на эти результаты — не выдумывай факты и цифры.
Отбрось нерелевантное нише. Пиши коротко и по делу, в тоне профиля.

ФОРМАТ ВЫВОДА (строго, без вступлений):

{TREND_MARKER}
🔥 <тренд 1> — чем полезен и как применить в блоге
🔥 <тренд 2> — …
🔥 <тренд 3> — …

ИДЕИ КОНТЕНТА:
1. <конкретная идея поста или Reels на основе тренда>
2. <ещё идея>
3. <ещё идея>

Выводи только блок после маркера {TREND_MARKER} и больше ничего."""


def brief_instruction(search_results: str) -> str:
    """Инструкция трендового брифа (V2.1, §20): 2-3 тренда + 3-5 тем постов.

    Формат жёсткий и парсимый: тело после {BRIEF_MARKER}, темы — строками
    {TOPIC_PREFIX}N по образцу HOOK_N (парсит parse_brief_topics()). Темы —
    самодостаточные формулировки поста (по ним потом соберётся пост с фактами
    из этого же поиска), а не абстрактные заголовки.
    """
    return f"""\
ЗАДАЧА: на основе результатов веб-поиска ниже собери короткий утренний бриф
трендов для Instagram-блога в нише и тоне профиля и предложи 3-5 тем постов.

РЕЗУЛЬТАТЫ ПОИСКА:
{search_results}

Опирайся ТОЛЬКО на эти результаты — не выдумывай факты, цифры и события.
Отбрось нерелевантное нише. Пиши коротко и по делу, в тоне профиля.
Каждая тема — конкретная, готовая к написанию поста формулировка (одна строка).

ФОРМАТ ВЫВОДА (строго, без вступлений и комментариев):

{BRIEF_MARKER}
🔥 <тренд 1> — чем важен для ниши
🔥 <тренд 2> — …
🔥 <тренд 3> — … (если есть)

{TOPIC_PREFIX}1: <тема поста одной строкой>
{TOPIC_PREFIX}2: <тема поста>
{TOPIC_PREFIX}3: <тема поста>
{TOPIC_PREFIX}4: <тема поста, если есть>
{TOPIC_PREFIX}5: <тема поста, если есть>

Выведи только блок после маркера {BRIEF_MARKER} и больше ничего."""


def parse_brief_topics(raw: str) -> list[str]:
    """Извлекает темы брифа из сырого ответа (строки ТЕМА_N: …).

    По образцу post_writer.parse_hooks(): устойчив к регистру, пробелам,
    обрамляющим кавычкам/звёздочкам. Если строк ТЕМА_ нет — fallback на
    нумерованный список «1.»/«1)». Возвращает список тем (обычно 3-5).
    """
    if not raw:
        return []

    topics: list[str] = []
    prefix_lower = TOPIC_PREFIX.lower()

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(prefix_lower):
            if ":" in stripped:
                value = stripped.split(":", 1)[1]
            else:
                value = stripped[len(TOPIC_PREFIX):].lstrip("0123456789").strip()
            value = _clean_topic(value)
            if value:
                topics.append(value)

    if not topics:
        for line in raw.splitlines():
            stripped = line.strip()
            if len(stripped) >= 2 and stripped[0].isdigit() and stripped[1] in ".)":
                value = _clean_topic(stripped[2:])
                if value:
                    topics.append(value)

    return topics


def _clean_topic(value: str) -> str:
    """Чистит текст темы: пробелы, обрамляющие кавычки и markdown-звёздочки."""
    value = value.strip()
    for quote in ('"', "«", "“", "”", "'"):
        if value.startswith(quote):
            value = value[1:]
        if value.endswith(quote) or value.endswith("»"):
            value = value[:-1]
    return value.strip().strip("*").strip()
