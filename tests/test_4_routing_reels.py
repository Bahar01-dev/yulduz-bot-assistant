"""ФУНКЦИЯ 4 — Роутинг интентов и выделение темы (agents/orchestrator.py).

Ошибка роутинга ломает весь UX: «сценарий»/«рилс» должны уходить в reel, а не в
post; «напиши пост про X» → intent=post и topic=X. Проверяем detect_intent,
приоритет reel над post, V2-интенты и extract_topic (границы слов).
"""

from agents.orchestrator import detect_intent, extract_topic, CLARIFY_TEXT


def test_detect_post():
    assert detect_intent("Напиши пост про осенний уход") == "post"


def test_detect_reel():
    assert detect_intent("Сделай рилс про утреннюю рутину") == "reel"
    assert detect_intent("Придумай сценарий для видео") == "reel"


def test_reel_priority_over_post():
    """«Сценарий»/«рилс» специфичнее «пост» → reel побеждает при совпадении обоих."""
    # Содержит и «пост», и «сценарий» — должен победить reel (по _INTENT_PRIORITY).
    assert detect_intent("сделай сценарий поста") == "reel"


def test_detect_v2_intents():
    assert detect_intent("Составь контент-план на неделю") == "plan"
    assert detect_intent("Что популярно у конкурентов?") == "trend"


def test_detect_unknown():
    assert detect_intent("Привет, как дела?") == "unknown"
    assert detect_intent("") == "unknown"


def test_extract_topic_strips_command_words():
    assert extract_topic("Напиши пост про осенний уход за кожей", "post") == "осенний уход за кожей"


def test_extract_topic_word_boundary():
    """«о» не должно срезать начало слова «осенний» (срез только по границе слова)."""
    topic = extract_topic("напиши пост про осенний уход", "post")
    assert topic == "осенний уход"
    assert topic.startswith("осенний")


def test_extract_topic_empty_when_only_commands():
    """Только командные слова без темы → пустая строка (бот попросит тему)."""
    assert extract_topic("напиши пост", "post") == ""


def test_clarify_text_present():
    """Текст вежливого уточнения не пустой (показывается при unknown)."""
    assert CLARIFY_TEXT and "пост" in CLARIFY_TEXT.lower()
