"""ФУНКЦИЯ 2 — Генерация поста: сборка промпта из профиля + парсинг хуков.

Главная ценность продукта. Тестируем детерминированные части пути «текст → пост»,
не требующие вызова LLM:
  • style_builder.build_system_prompt — профиль БД целиком уходит в system-промпт;
  • post_writer.parse_hooks — ответ модели «HOOK_1/2/3» → список из 3 хуков.
"""

from prompts.style_builder import build_system_prompt
from agents.post_writer import parse_hooks


PROFILE = {
    "niche": "уход за кожей",
    "target_audience": "женщины 25-40",
    "tone": "тёплый, экспертный",
    "forbidden_words": "дешёвый, химия",
    "reference_accounts": "@skincare_guru",
    "style_description": "разговорный, с примерами",
}


def test_prompt_contains_all_profile_fields():
    """Все поля профиля попадают в системный промпт (иначе бот пишет не в стиле)."""
    prompt = build_system_prompt(PROFILE, format_instructions="ФОРМАТ")
    assert "уход за кожей" in prompt
    assert "женщины 25-40" in prompt
    assert "тёплый, экспертный" in prompt
    assert "дешёвый, химия" in prompt
    assert "@skincare_guru" in prompt      # референс дописан к стиль-ориентиру
    assert "ФОРМАТ" in prompt              # format_instructions подставлены


def test_prompt_empty_profile_has_placeholders():
    """Пустой профиль не роняет сборку — подставляются заглушки, а не пусто/None."""
    prompt = build_system_prompt({}, format_instructions="X")
    assert "(не задана)" in prompt          # ниша/аудитория
    assert "None" not in prompt             # None не должен утечь в текст промпта


def test_prompt_recent_topics_dedup():
    """История последних тем передаётся для дедупликации."""
    prompt = build_system_prompt(PROFILE, recent_topics=["осень", "SPF летом"])
    assert "осень" in prompt
    assert "SPF летом" in prompt


def test_prompt_empty_topics_placeholder():
    """Без истории — осмысленная заглушка «первая публикация»."""
    prompt = build_system_prompt(PROFILE, recent_topics=[])
    assert "первая публикация" in prompt


def test_style_examples_block_injected():
    """Оценки 👍/👎 (V2) подмешиваются в промпт как примеры хорошего/плохого стиля."""
    examples = {"approved": ["Живой хук про утро"], "rejected": ["Скучное вступление"]}
    prompt = build_system_prompt(PROFILE, style_examples=examples)
    assert "ОБУЧЕНИЕ СТИЛЮ" in prompt
    assert "Живой хук про утро" in prompt
    assert "Скучное вступление" in prompt


def test_parse_three_hooks():
    """Ответ модели в формате HOOK_1/2/3 → ровно 3 очищенных хука."""
    raw = 'HOOK_1: Первый хук\nHOOK_2: "Второй хук"\nHOOK_3: **Третий хук**'
    hooks = parse_hooks(raw)
    assert hooks == ["Первый хук", "Второй хук", "Третий хук"]


def test_parse_hooks_numbered_fallback():
    """Если модель отдала нумерованный список — парсер всё равно достаёт хуки."""
    raw = "1. Первый\n2) Второй\n3. Третий"
    hooks = parse_hooks(raw)
    assert hooks == ["Первый", "Второй", "Третий"]


def test_parse_hooks_empty():
    """Пустой ответ → пустой список (вызывающий код попросит переформулировать)."""
    assert parse_hooks("") == []
    assert parse_hooks("бла-бла без хуков") == []
