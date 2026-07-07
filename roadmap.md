# Roadmap — технический долг и улучшения

Отложенные находки код-ревью (не срочные). Срочные пункты (незащищённая запись
профиля при онбординге + голый INSERT с UNIQUE) уже исправлены — см. коммит
`fix(onboarding)`.

Легенда статуса: 📋 запланировано · 🚧 в работе · ✅ сделано

---

## 🟡 Можно позже (рефакторинг, надёжность на краях)

| # | Статус | Что | Где | Почему важно |
|---|--------|-----|-----|--------------|
| 1 | 📋 | Свернуть дубли двух почти идентичных потоков «пост» и «Reels» | `handlers/callbacks.py` (`_generate_and_show_post`↔`_generate_and_show_reel`, `on_post_save`↔`on_reel_save`), `handlers/message.py` (`handle_post_request`↔`handle_reel_request`), `agents/post_writer.parse_hooks`↔`prompts/trend_prompt.parse_brief_topics`, тоггл тона в `handlers/start.py`↔`handlers/menu.py` | Правку легко внести в одном месте и забыть в другом. Вынести общий «движок потока» с параметрами (writer, keyboards, тексты, state) |
| 2 | 📋 | Убрать дубли строковых констант | `NO_PROFILE_TEXT` (message/menu/plan/trends), `MENU_PROMPT` (start/callbacks/menu), `ASK_POST_TOPIC`/`ASK_REEL_TOPIC` (callbacks/message) | Один модуль `handlers/texts.py` — единый источник текстов |
| 3 | 📋 | Устранить скрытый цикл импортов между `message` и `callbacks` | `handlers/message.py` импортит `_render_hooks_message` из callbacks, а `callbacks.py` вынужден делать локальный `from handlers.message import handle_post_request` | Вынести общие функции (`_render_hooks_message`, `handle_post_request`, публичный `escape_md`) в отдельный модуль. Убрать кросс-импорт «приватных» `_`-функций |
| 4 | 📋 | Удалить мёртвый код V2, вытесненный V2.1-брифом | `agents/trend_analyst.generate_trends`, `utils/formatter.format_trends`, `utils/keyboards.trend_actions_keyboard`, `prompts/trend_prompt.trend_instruction`/`TREND_MARKER` | Не используются нигде (handlers/trends.py работает через `generate_brief`+`brief_keyboard`). Лишняя поверхность поддержки |
| 5 | 📋 | Сузить матчинг интентов в `detect_intent` | `agents/orchestrator.py` | Подстрочный поиск («анализ», «конкуренты», «план на») даёт ложные срабатывания («сделай анализ ошибок» → trend). Матчить по границам слов |
| 6 | 📋 | Согласовать часовой пояс даты в промпте | `prompts/style_builder._today()` использует наивный `datetime.now()`, а брифы/кэш — `TREND_TZ` | На стыке суток модель считает «сегодня»/год иначе, чем дата брифа. Передавать `TREND_TZ` и в `_current_date_line` |
| 7 | 📋 | Переиспользовать клиентов внешних API | `utils/errors.py`: `AsyncGroq(...)` и `AsyncTavilyClient(...)` создаются на каждый вызов | Пул соединений не переиспользуется. Вынести в модульные синглтоны (как LLM-клиент) |

---

## ⚪ Некритично (косметика, мелочи)

| # | Статус | Что | Где |
|---|--------|-----|-----|
| 8 | 📋 | Обновить устаревшие докстринги «4 таблицы» (фактически 5) | `database/db.py` (докстринги модуля и `init_db`), комментарии в `main.py` |
| 9 | 📋 | Добавить логирование в «немые» `except Exception: pass` у `edit_reply_markup` | `handlers/callbacks.py` (строки с `edit_reply_markup`), хотя бы `logger.debug(..., exc_info=True)` |
| 10 | 📋 | Фильтровать черновик по владельцу в самом запросе | `database/drafts.get()` (сейчас владелец проверяется только в хэндлере) |
| 11 | 📋 | Обернуть `json.loads` в `try` для единообразия | `database/drafts.py`, `database/publications.py` (в `trend_brief.get` уже с `try`) |
| 12 | 📋 | Переиспользовать `_load_profile_and_topics` | `handlers/message.py`, `handlers/plan.py` грузят те же 3 источника вручную вместо хелпера из `callbacks.py` |

---

## 🧪 Тех.долг тестов

| # | Статус | Что | Где |
|---|--------|-----|-----|
| 13 | 📋 | (Опционально) вместо `pytest.ini` пометить async-фикстуру декоратором | `tests/conftest.py`: `clean_db` → `@pytest_asyncio.fixture`. Сейчас режим включён через `pytest.ini` (`asyncio_mode = auto`) — работает; это альтернативный способ той же настройки |
| 14 | 📋 | Добавить тесты на потоки хэндлеров (post/reel/callbacks) | `tests/` — сейчас покрыты в основном БД-слой и промпты |
