"""FSM-состояния диалога (aiogram FSM).

Фаза 4: состояния онбординга (5 шагов, spec.md §7.1).
Группы для будущих фаз (генерация поста/Reels) добавлены заглушками.
"""

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    """5 шагов онбординга (spec.md §7.1).

    niche      — шаг 1: ниша (текст)
    audience   — шаг 2: целевая аудитория (текст)
    tone       — шаг 3: тон (мультивыбор inline-кнопками)
    forbidden  — шаг 4: запрещённые слова (текст или «Пропустить»)
    refs       — шаг 5: 1-3 референс-аккаунта (текст)
    """

    niche = State()
    audience = State()
    tone = State()
    forbidden = State()
    refs = State()


class PostFlow(StatesGroup):
    """Состояния генерации поста (Фаза 6, spec.md §7.2).

    waiting_topic — ждём тему поста (после кнопки меню «✍️ Написать пост»);
    choosing_hook — показаны 3 хука, ждём выбор кнопкой (свободный текст не реролит);
    viewing_post  — показан готовый пост, активны кнопки действий.

    В FSM data во время потока храним:
      topic           — тема поста (str);
      hooks           — список хуков (list[str]);
      selected_hook   — индекс выбранного хука (int);
      last_post_text  — последний сгенерированный пост (сырой ответ модели, str).
    """

    waiting_topic = State()
    choosing_hook = State()
    viewing_post = State()


class ReelFlow(StatesGroup):
    """Состояния генерации сценария Reels (Фаза 7, spec.md §7.3).

    Зеркалит PostFlow: тема → 3 хука → выбор → сценарий с таймингами.

    waiting_topic — ждём тему Reels (после кнопки меню «🎬 Сценарий Reels»);
    choosing_hook — показаны 3 хука, ждём выбор кнопкой (свободный текст не реролит);
    viewing_reel  — показан готовый сценарий, активны кнопки действий.

    В FSM data во время потока храним:
      topic           — тема Reels (str);
      hooks           — список хуков (list[str]);
      selected_hook   — индекс выбранного хука (int);
      duration        — выбранная длина в секундах (15/30/60, int);
      last_reel_text  — последний сгенерированный сценарий (сырой ответ модели, str).
    """

    waiting_topic = State()
    choosing_hook = State()
    viewing_reel = State()


class ProfileEdit(StatesGroup):
    """Состояния редактирования профиля (Фаза 8, spec.md §10).

    choosing_field — показано меню выбора поля для изменения;
    editing_text   — ждём новый текст для текстового поля (ниша/аудитория/
                     запреты/референсы); какое именно поле — в data['edit_field'];
    editing_tone   — мультивыбор тона (как в онбординге); выбранное — в data['tones'].

    В FSM data во время редактирования храним:
      edit_field — ключ редактируемого текстового поля (str);
      tones      — список выбранных тонов при editing_tone (list[str]).
    """

    choosing_field = State()
    editing_text = State()
    editing_tone = State()
