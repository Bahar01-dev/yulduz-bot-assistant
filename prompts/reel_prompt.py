"""Инструкции формата для сценариев Reels (spec.md §9.2, §9.3, §13.1).

Двухшаговая генерация (как у постов):
  ШАГ 1 — hooks_instruction(): отдельный вызов, РОВНО 3 хука для первых 3 секунд.
  ШАГ 2 — reel_format_instruction(selected_hook, duration_sec): побитовый
          сценарий строго по §9.2 — ХУК (0-3 сек), БИТ 1, БИТ 2…, CTA
          (последние 5 сек); каждый бит с 🎙 Голос / 📺 На экране / 🎬 Визуал;
          затем ПОДПИСЬ и ХЭШТЕГИ. Количество битов — под хронометраж.

Инструкции подставляются в {format_instructions} системного промпта из
style_builder.build_system_prompt().
"""

# Temperature для Reels — чуть больше креативности (§13.1)
REEL_TEMPERATURE = 0.8

# Поддерживаемые хронометражи (§17, открытый вопрос 2) и дефолт
SUPPORTED_DURATIONS = (15, 30, 60)
DEFAULT_DURATION = 30

# Маркеры/метки — formatter (Фаза 6) парсит по ним. НЕ менять без правки парсера.
HOOK_PREFIX = "HOOK_"          # ШАГ 1
SCENARIO_MARKER = "СЦЕНАРИЙ REELS"
HOOK_LABEL = "ХУК"
BIT_LABEL = "БИТ"
CTA_LABEL = "CTA"
VOICE_LABEL = "🎙 Голос:"
SCREEN_LABEL = "📺 На экране:"
VISUAL_LABEL = "🎬 Визуал:"
CAPTION_MARKER = "ПОДПИСЬ:"
HASHTAGS_MARKER = "ХЭШТЕГИ:"

# Рекомендуемое число содержательных битов (между ХУКом и CTA) под хронометраж.
_BITS_BY_DURATION = {15: 1, 30: 2, 60: 4}


def _bits_for(duration_sec: int) -> int:
    """Сколько битов закладывать под заданную длину."""
    return _BITS_BY_DURATION.get(duration_sec, 2)


def hooks_instruction(topic: str) -> str:
    """ШАГ 1: РОВНО 3 варианта хука для первых 3 секунд Reels.

    Формат жёсткий (HOOK_N построчно) для тривиального парсинга.
    """
    return f"""\
ЗАДАЧА (ШАГ 1 — только хуки):
Тема Reels: «{topic}»

Придумай РОВНО 3 разных варианта хука для ПЕРВЫХ 3 СЕКУНД видео.
Хук должен мгновенно останавливать скролл и удерживать внимание.
Каждый вариант — под своим углом (например: боль зрителя, обещание, интрига).

ФОРМАТ ВЫВОДА (строго, без вступлений и комментариев):
HOOK_1: <текст первого хука>
HOOK_2: <текст второго хука>
HOOK_3: <текст третьего хука>

Выведи ровно три строки HOOK_1, HOOK_2, HOOK_3 и больше ничего."""


def reel_format_instruction(selected_hook: str, duration_sec: int = DEFAULT_DURATION) -> str:
    """ШАГ 2: побитовый сценарий Reels по выбранному хуку (формат §9.2 + §9.3).

    Args:
        selected_hook: выбранный пользователем хук (текст для 0-3 сек).
        duration_sec: хронометраж (15/30/60). Влияет на число битов и тайминги.
    """
    if duration_sec not in SUPPORTED_DURATIONS:
        duration_sec = DEFAULT_DURATION

    n_bits = _bits_for(duration_sec)
    cta_start = duration_sec - 5  # CTA — последние 5 секунд (§9.2)

    # Подсказка по таймингам битов: равномерно делим окно [3 .. cta_start].
    span = max(cta_start - 3, 1)
    step = span / n_bits
    bit_hints = []
    for i in range(n_bits):
        start = round(3 + step * i)
        end = round(3 + step * (i + 1))
        bit_hints.append(f"БИТ {i + 1} (≈{start}–{end} сек)")
    bits_plan = ", ".join(bit_hints)

    return f"""\
ЗАДАЧА (ШАГ 2 — полный сценарий Reels):
Пользователь выбрал этот хук (первые 3 секунды):
«{selected_hook}»

Напиши побитовый сценарий Reels на {duration_sec} секунд на основе ИМЕННО этого хука.
Заложи {n_bits} содержательных бит(а) между хуком и CTA. Ориентир по таймингам: {bits_plan}.
CTA — последние 5 секунд (с {cta_start}-й по {duration_sec}-ю секунду).

В КАЖДОМ блоке (ХУК, каждый БИТ, CTA) обязательно три строки:
{VOICE_LABEL} <что говорится за кадром>
{SCREEN_LABEL} <текст-плашка на экране>
{VISUAL_LABEL} <что показываем в кадре>

ФОРМАТ ВЫВОДА (строго по разделителям и меткам):

📖 *{SCENARIO_MARKER}* ({duration_sec} сек)

━━━ {HOOK_LABEL} (0–3 сек) ━━━
{VOICE_LABEL} {selected_hook}
{SCREEN_LABEL} <короткая плашка>
{VISUAL_LABEL} <визуал>

━━━ {BIT_LABEL} 1 (3–… сек) ━━━
{VOICE_LABEL} <…>
{SCREEN_LABEL} <…>
{VISUAL_LABEL} <…>

[… добавь остальные БИТ-блоки по той же схеме, всего {n_bits} бит(а) …]

━━━ {CTA_LABEL} (последние 5 сек) ━━━
{VOICE_LABEL} <один призыв к действию, без воды>
{SCREEN_LABEL} <короткая плашка с действием>
{VISUAL_LABEL} <визуал, например прямой взгляд в камеру>

{CAPTION_MARKER}
<готовый текст подписи под Reels в тоне профиля>

{HASHTAGS_MARKER}
<5-10 релевантных хэштегов в одну строку через пробел, начиная с #>

Соблюдай метки 🎙/📺/🎬 и разделители ━━━ дословно. Ничего лишнего не добавляй."""
