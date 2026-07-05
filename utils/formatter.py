"""Форматирование сырого ответа модели в Telegram-Markdown (Фаза 6, spec.md §9.1/§9.3).

format_post(raw) парсит ответ ШАГА 2 по маркерам секций
(POST:/CTA:/ХЭШТЕГИ:/ЧЕКЛИСТ:) и собирает аккуратный вывод:

    📝 *ПОСТ*

    {хук + тело}

    ---
    *CTA:* {призыв}

    ---
    *ХЭШТЕГИ:*
    {хэштеги}

    ---
    ✅ *Чеклист:*
    {пункты}

Бот работает в parse_mode=MARKDOWN (legacy). Чтобы тело поста не ломало
разметку, экранируем «опасные» одиночные символы (* _ `) в пользовательском
тексте. Структурные заголовки (`*ПОСТ*`, `*CTA:*` …) формируем сами и не
экранируем — они корректны по определению.

Fallback: если ни одного маркера не найдено — возвращаем исходный текст как
есть (экранированный), чтобы ничего не потерять и не упасть.
"""

from __future__ import annotations

import re

from prompts.plan_prompt import PLAN_MARKER, period_title
from prompts.trend_prompt import BRIEF_MARKER, TOPIC_PREFIX, TREND_MARKER
from prompts.post_prompt import (
    CHECKLIST_MARKER,
    CTA_MARKER,
    HASHTAGS_MARKER,
    POST_MARKER,
)
from prompts.reel_prompt import (
    CAPTION_MARKER,
    HASHTAGS_MARKER as REEL_HASHTAGS_MARKER,
    SCENARIO_MARKER,
    SCREEN_LABEL,
    VISUAL_LABEL,
    VOICE_LABEL,
)

# Разделитель секций (как в §9.1).
_DIVIDER = "---"


def _escape_md(text: str) -> str:
    """Экранирует символы legacy-Markdown Telegram в пользовательском тексте.

    Telegram legacy MARKDOWN ломается на непарных * _ ` [ — экранируем их
    обратным слешем. Так тело поста, CTA и хэштеги выводятся буквально, не
    нарушая нашу структурную разметку.
    """
    if not text:
        return ""
    out = []
    for ch in text:
        if ch in ("*", "_", "`", "["):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _split_sections(raw: str) -> dict[str, str]:
    """Разбивает сырой ответ по маркерам секций в словарь.

    Идём построчно: встретив строку-маркер (POST:/CTA:/ХЭШТЕГИ:/ЧЕКЛИСТ:),
    переключаем «текущую секцию» и копим её содержимое до следующего маркера.
    Маркер распознаётся, даже если на той же строке идёт текст после него.

    Возвращает dict с ключами 'post'/'cta'/'hashtags'/'checklist' (только
    найденные). Если маркеров нет — возвращает пустой dict (→ fallback).
    """
    # Соответствие маркера → ключ секции
    markers = {
        POST_MARKER: "post",
        CTA_MARKER: "cta",
        HASHTAGS_MARKER: "hashtags",
        CHECKLIST_MARKER: "checklist",
    }

    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        matched = None
        for marker, key in markers.items():
            if stripped == marker or stripped.startswith(marker):
                matched = (marker, key)
                break
        if matched is not None:
            marker, key = matched
            current = key
            sections.setdefault(key, [])
            # Текст, идущий на той же строке после маркера, тоже учитываем
            tail = stripped[len(marker):].strip()
            if tail:
                sections[key].append(tail)
            continue
        if current is not None:
            sections[current].append(line)

    # Склеиваем строки секций и подчищаем края
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def format_post(raw: str) -> str:
    """Собирает красивый Telegram-Markdown пост из сырого ответа модели.

    При отсутствии маркеров — возвращает экранированный исходный текст (fallback).
    """
    if not raw or not raw.strip():
        return ""

    sections = _split_sections(raw)

    # Fallback: маркеры не распознаны — отдаём текст как есть (экранированный).
    if not sections:
        return _escape_md(raw.strip())

    blocks: list[str] = []

    # 📝 ПОСТ (хук + тело идут одним блоком, как в §9.1)
    post_body = sections.get("post", "").strip()
    blocks.append("📝 *ПОСТ*")
    if post_body:
        blocks.append("")
        blocks.append(_escape_md(post_body))

    # CTA
    cta = sections.get("cta", "").strip()
    if cta:
        blocks.append("")
        blocks.append(_DIVIDER)
        blocks.append(f"*CTA:* {_escape_md(cta)}")

    # ХЭШТЕГИ (отдельным блоком, чтобы скопировать отдельно)
    hashtags = sections.get("hashtags", "").strip()
    if hashtags:
        blocks.append("")
        blocks.append(_DIVIDER)
        blocks.append("*ХЭШТЕГИ:*")
        blocks.append(_escape_md(hashtags))

    # ✅ Чеклист (§9.3)
    checklist = sections.get("checklist", "").strip()
    if checklist:
        blocks.append("")
        blocks.append(_DIVIDER)
        blocks.append("✅ *Чеклист:*")
        blocks.append(_escape_md(checklist))

    return "\n".join(blocks).strip()


# ───────────────────────── Контент-план (V2, §18) ─────────────────────────


def format_plan(raw: str, period: str = "week") -> str:
    """Собирает красивый Telegram-Markdown контент-план из сырого ответа модели.

    Срезает маркер PLAN_MARKER (если есть), экранирует тело и добавляет заголовок
    «🗓 КОНТЕНТ-ПЛАН на <период>». При пустом вводе — пустая строка; маркера может
    не быть — тогда форматируем весь ответ как тело (ничего не теряем).
    """
    if not raw or not raw.strip():
        return ""

    body = raw.strip()
    # Срезаем всё до маркера ПЛАН: включительно, если модель его поставила.
    idx = body.find(PLAN_MARKER)
    if idx != -1:
        body = body[idx + len(PLAN_MARKER):].strip()

    header = f"🗓 *КОНТЕНТ-ПЛАН на {period_title(period)}*"
    if not body:
        return header
    return f"{header}\n\n{_escape_md(body)}"


# ───────────────────────── Тренды (V2, §18) ─────────────────────────


def format_trends(raw: str) -> str:
    """Собирает Telegram-Markdown разбор трендов из сырого ответа модели.

    Срезает маркер TREND_MARKER (если есть), экранирует тело и добавляет
    заголовок «📈 ТРЕНДЫ». При пустом вводе — пустая строка.
    """
    if not raw or not raw.strip():
        return ""

    body = raw.strip()
    idx = body.find(TREND_MARKER)
    if idx != -1:
        body = body[idx + len(TREND_MARKER):].strip()

    header = "📈 *ТРЕНДЫ И ИДЕИ*"
    if not body:
        return header
    return f"{header}\n\n{_escape_md(body)}"


# ─────────────────── Трендовый бриф по расписанию (V2.1, §20) ───────────────────


def format_brief(raw: str) -> str:
    """Собирает Telegram-Markdown утренний трендовый бриф из сырого ответа модели.

    Срезает маркер BRIEF_MARKER (если есть); строки тем «ТЕМА_N: …» превращает в
    нумерованный список «N. …» (кнопки под сообщением дублируют выбор), остальные
    строки (тренды 🔥) экранирует как есть. Добавляет заголовок и подсказку. При
    пустом вводе — пустая строка (вызывающий слой обработает как сбой).
    """
    if not raw or not raw.strip():
        return ""

    body = raw.strip()
    idx = body.find(BRIEF_MARKER)
    if idx != -1:
        body = body[idx + len(BRIEF_MARKER):].strip()

    prefix_lower = TOPIC_PREFIX.lower()
    topic_no = 0
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.lower().startswith(prefix_lower):
            topic_no += 1
            if ":" in stripped:
                value = stripped.split(":", 1)[1].strip()
            else:
                value = stripped[len(TOPIC_PREFIX):].lstrip("0123456789").strip()
            lines.append(f"*{topic_no}.* {_escape_md(value)}")
        else:
            lines.append(_escape_md(stripped))

    # Убираем хвостовые пустые строки
    while lines and lines[-1] == "":
        lines.pop()

    header = "🌅 *ТРЕНДЫ ДНЯ*"
    footer = "Нажми на тему ниже — соберу пост на реальных фактах из этого брифа."
    parts = [header, ""]
    parts.extend(lines)
    if topic_no:
        parts.append("")
        parts.append(footer)
    return "\n".join(parts).strip()


# ───────────────────────── Reels (Фаза 7, §9.2) ─────────────────────────

# Метки слоёв внутри блока сценария (🎙/📺/🎬) — выделяем жирным.
_REEL_LAYER_LABELS = (VOICE_LABEL, SCREEN_LABEL, VISUAL_LABEL)


def _render_scenario_block(scenario_lines: list[str]) -> list[str]:
    """Рендерит тело сценария (до ПОДПИСЬ:) построчно в Telegram-Markdown.

    Заголовок сценария и заголовки блоков (━━━ ХУК/БИТ/CTA ━━━) выделяются
    жирным как структура; метки слоёв (🎙/📺/🎬) — жирные, их текст экранируется;
    пустые строки сохраняются как разделители блоков.
    """
    out: list[str] = []
    for raw_line in scenario_lines:
        stripped = raw_line.strip()
        if not stripped:
            out.append("")
            continue
        # Заголовок «СЦЕНАРИЙ REELS (N сек)» — собираем сами (длину достаём regex).
        if SCENARIO_MARKER in stripped:
            m = re.search(r"\((\d+)\s*сек\)", stripped)
            dur = f" ({m.group(1)} сек)" if m else ""
            out.append(f"🎬 *{SCENARIO_MARKER}*{dur}")
            continue
        # Заголовок блока вида «━━━ ХУК (0–3 сек) ━━━» → жирный без декора ━.
        if "━" in stripped:
            inner = stripped.replace("━", "").strip()
            if inner:
                out.append(f"*{_escape_md(inner)}*")
            continue
        # Строки-слои 🎙/📺/🎬: метка жирная, содержимое экранируем.
        matched_label = None
        for label in _REEL_LAYER_LABELS:
            if stripped.startswith(label):
                matched_label = label
                break
        if matched_label is not None:
            rest = stripped[len(matched_label):].strip()
            out.append(f"*{matched_label}* {_escape_md(rest)}")
            continue
        # Прочий текст — просто экранируем.
        out.append(_escape_md(stripped))
    return out


def format_reel(raw: str) -> str:
    """Собирает красивый Telegram-Markdown сценарий Reels из сырого ответа (§9.2).

    Разбивает ответ на три части: тело сценария (до ПОДПИСЬ:), подпись и хэштеги;
    тело форматирует _render_scenario_block. При отсутствии меток/маркеров —
    возвращает экранированный исходный текст (fallback), чтобы ничего не потерять.
    """
    if not raw or not raw.strip():
        return ""

    scenario_lines: list[str] = []
    caption_lines: list[str] = []
    hashtags_lines: list[str] = []
    current = "scenario"
    seen_marker = False

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(CAPTION_MARKER):
            current = "caption"
            seen_marker = True
            tail = stripped[len(CAPTION_MARKER):].strip()
            if tail:
                caption_lines.append(tail)
            continue
        if stripped.startswith(REEL_HASHTAGS_MARKER):
            current = "hashtags"
            seen_marker = True
            tail = stripped[len(REEL_HASHTAGS_MARKER):].strip()
            if tail:
                hashtags_lines.append(tail)
            continue
        if current == "scenario":
            scenario_lines.append(line)
        elif current == "caption":
            caption_lines.append(line)
        else:
            hashtags_lines.append(line)

    # Fallback: ни меток слоёв, ни маркеров — структуру не распознали.
    has_layers = any(lbl in raw for lbl in _REEL_LAYER_LABELS)
    if not seen_marker and not has_layers and SCENARIO_MARKER not in raw:
        return _escape_md(raw.strip())

    blocks = _render_scenario_block(scenario_lines)
    # Убираем хвостовые пустые строки тела сценария
    while blocks and blocks[-1] == "":
        blocks.pop()

    caption = "\n".join(caption_lines).strip()
    if caption:
        blocks.append("")
        blocks.append(_DIVIDER)
        blocks.append("📄 *ПОДПИСЬ:*")
        blocks.append(_escape_md(caption))

    hashtags = "\n".join(hashtags_lines).strip()
    if hashtags:
        blocks.append("")
        blocks.append(_DIVIDER)
        blocks.append("*ХЭШТЕГИ:*")
        blocks.append(_escape_md(hashtags))

    return "\n".join(blocks).strip()
