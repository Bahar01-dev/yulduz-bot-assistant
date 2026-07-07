"""ФУНКЦИЯ 5 — Обучение стилю (style_feedback) + устойчивость LLM (safe_generate).

Две вещи, что делают бота «умнее» и стабильнее:
  • style_feedback.save/get_examples — оценки 👍/👎 копятся и отдаются в промпт;
  • safe_generate — обёртка LLM: успех → текст; любая ошибка → дружелюбное RU-
    сообщение (пользователь НИКОГДА не видит тех.текст), с retry по каталогу.

Конфиг проекта: LLM_BASE_URL задан → путь OpenAI-совместимый (client.chat.
completions.create). Поэтому фейковый клиент реализует именно этот интерфейс.
"""

import pytest

from database import style_feedback
from utils import errors
from utils.errors import safe_generate, classify_exception, Situation, get_policy

pytestmark_asyncio = pytest.mark.asyncio


# ─────────────────────── Обучение стилю (БД) ───────────────────────

@pytest.mark.asyncio
async def test_style_feedback_roundtrip(clean_db, owner_id):
    """👍/👎 сохраняются и возвращаются как approved/rejected примеры."""
    await style_feedback.save(owner_id, "Хороший живой хук", style_feedback.APPROVED)
    await style_feedback.save(owner_id, "Скучное вступление", style_feedback.REJECTED)

    examples = await style_feedback.get_examples(owner_id)
    assert "Хороший живой хук" in examples["approved"]
    assert "Скучное вступление" in examples["rejected"]


@pytest.mark.asyncio
async def test_style_feedback_invalid_verdict(clean_db, owner_id):
    """Недопустимый вердикт → ValueError (защита от мусора в БД)."""
    with pytest.raises(ValueError):
        await style_feedback.save(owner_id, "текст", "maybe")


@pytest.mark.asyncio
async def test_style_feedback_snippet_truncated(clean_db, owner_id):
    """Длинный фрагмент обрезается (~600), чтобы не раздувать system-промпт."""
    long_text = "а" * 1000
    await style_feedback.save(owner_id, long_text, style_feedback.APPROVED)
    examples = await style_feedback.get_examples(owner_id)
    saved = examples["approved"][0]
    assert len(saved) < 1000
    assert saved.endswith("…")


@pytest.mark.asyncio
async def test_style_feedback_empty_when_none(clean_db, owner_id):
    """Без фидбэка — пустые списки (промпт не меняется относительно V1)."""
    examples = await style_feedback.get_examples(owner_id)
    assert examples == {"approved": [], "rejected": []}


# ─────────────────────── Фейковый LLM-клиент (OpenAI-путь) ───────────────────────

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    """create() отдаёт по очереди значения из behaviors: строка → ответ модели,
    исключение → бросок. Считает число вызовов (для проверки retry)."""

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.calls = 0

    async def create(self, **params):
        self.calls += 1
        item = self._behaviors[min(self.calls - 1, len(self._behaviors) - 1)]
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


class _Chat:
    def __init__(self, behaviors):
        self.completions = _Completions(behaviors)


class _FakeClient:
    def __init__(self, behaviors):
        self.chat = _Chat(behaviors)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Убираем реальные задержки backoff, чтобы retry-тесты шли мгновенно."""
    async def _instant(_attempt):
        return None
    monkeypatch.setattr(errors, "_sleep_backoff", _instant)


# ─────────────────────── safe_generate ───────────────────────

@pytest.mark.asyncio
async def test_safe_generate_success():
    """Модель ответила текстом → ok=True, текст возвращён."""
    client = _FakeClient(["Готовый пост про уход"])
    result = await safe_generate(
        messages=[{"role": "user", "content": "тема"}],
        system="системный промпт",
        client=client,
    )
    assert result.ok is True
    assert result.text == "Готовый пост про уход"
    assert client.chat.completions.calls == 1


@pytest.mark.asyncio
async def test_safe_generate_empty_retries_then_fails():
    """Пустой ответ → EMPTY_RESPONSE с одним retry (2 вызова), затем дружелюбная ошибка."""
    client = _FakeClient(["", ""])  # оба раза пусто
    result = await safe_generate(
        messages=[{"role": "user", "content": "тема"}],
        client=client,
    )
    assert result.ok is False
    assert result.situation == Situation.EMPTY_RESPONSE.value
    assert client.chat.completions.calls == 2  # исходный + 1 retry (retries=1)
    # Пользователь видит человеческий текст, а не тех.детали.
    assert result.user_message
    assert "🔄" in result.user_message or "Переписать" in result.user_message


@pytest.mark.asyncio
async def test_safe_generate_hides_technical_error():
    """Любое исключение → ok=False, RU-сообщение без тех.текста, ситуация UNKNOWN."""
    boom = RuntimeError("Traceback: KeyError at 0x7ffff")
    client = _FakeClient([boom])
    result = await safe_generate(
        messages=[{"role": "user", "content": "тема"}],
        client=client,
    )
    assert result.ok is False
    assert result.situation == Situation.UNKNOWN.value
    assert result.alert_owner is True                       # владельцу — алерт
    assert "Traceback" not in (result.user_message or "")   # пользователю — нет
    assert result.text is None


def test_classify_plain_exception_is_unknown():
    """Не-SDK исключение классифицируется как catch-all UNKNOWN."""
    assert classify_exception(ValueError("x")) == Situation.UNKNOWN


def test_rate_limit_policy_no_alert_no_retry():
    """RATE_LIMIT: понятный RU-текст, без алерта владельцу и без retry."""
    policy = get_policy(Situation.RATE_LIMIT)
    assert policy.alert_owner is False
    assert policy.retries == 0
    assert "Слишком много запросов" in policy.user_message
