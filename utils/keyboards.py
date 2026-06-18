"""Inline-клавиатуры aiogram (онбординг + главное меню, spec.md §10).

Колбэки онбординга:
  onboarding_start          — кнопка «Поехали →»
  tone:<Название>           — переключение варианта тона (мультивыбор, шаг 3)
  tone_done                 — завершение выбора тона
  skip_forbidden            — пропустить шаг 4 (запрещённые слова)

Колбэки главного меню (§10):
  menu:post    — написать пост (Фаза 6)
  menu:reel    — сценарий Reels (Фаза 8)
  menu:drafts  — мои черновики
  menu:profile — изменить профиль
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from agents.onboarding import TONE_OPTIONS
from prompts.reel_prompt import SUPPORTED_DURATIONS


def start_onboarding_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Поехали →» под приветствием (шаг старта онбординга)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Поехали →", callback_data="onboarding_start")
    return builder.as_markup()


def tone_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура мультивыбора тона (шаг 3).

    selected — список уже выбранных тонов (из FSM data). У выбранных
    отображается галочка ✓. Внизу — кнопка «✅ Готово» (tone_done).
    """
    builder = InlineKeyboardBuilder()
    for option in TONE_OPTIONS:
        # Галочка у выбранных вариантов
        prefix = "✓ " if option in selected else ""
        builder.button(
            text=f"{prefix}{option}",
            callback_data=f"tone:{option}",
        )
    # Кнопка завершения выбора
    builder.button(text="✅ Готово", callback_data="tone_done")
    # По одной кнопке в ряд (длинные названия тонов) + «Готово» отдельной строкой
    builder.adjust(1)
    return builder.as_markup()


def skip_forbidden_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Пропустить» для шага 4 (запрещённые слова)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="skip_forbidden")
    return builder.as_markup()


def hooks_keyboard(hooks: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора хука (Фаза 6, §7.2).

    Сами тексты хуков показываются в сообщении; кнопки подписаны «Хук 1/2/3».
    callback_data: post_hook:0 / post_hook:1 / post_hook:2 — индекс в списке.
    Создаём столько кнопок, сколько реально пришло хуков (обычно 3).
    """
    builder = InlineKeyboardBuilder()
    for idx in range(len(hooks)):
        builder.button(text=f"Хук {idx + 1}", callback_data=f"post_hook:{idx}")
    # Все кнопки в один ряд
    builder.adjust(len(hooks) or 1)
    return builder.as_markup()


def post_actions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура действий под готовым постом (spec.md §10).

    Раскладка:
      [🔄 Переписать]  [💡 Другой вариант]
      [🪝 Другой хук]  [💾 Сохранить]
      [🏠 В меню]

    callback_data согласованы с handlers/callbacks.py:
      post:rewrite / post:another / post:another_hook / post:save / menu
    """
    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Переписать", callback_data="post:rewrite"),
            InlineKeyboardButton(text="💡 Другой вариант", callback_data="post:another"),
        ],
        [
            InlineKeyboardButton(text="🪝 Другой хук", callback_data="post:another_hook"),
            InlineKeyboardButton(text="💾 Сохранить", callback_data="post:save"),
        ],
        [
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def reel_hooks_keyboard(hooks: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора хука для Reels (Фаза 7, §7.3).

    Аналог hooks_keyboard, но callback_data с префиксом reel_hook:<idx>, чтобы
    поток Reels не пересекался с потоком поста (post_hook:*).
    """
    builder = InlineKeyboardBuilder()
    for idx in range(len(hooks)):
        builder.button(text=f"Хук {idx + 1}", callback_data=f"reel_hook:{idx}")
    builder.adjust(len(hooks) or 1)
    return builder.as_markup()


def reel_actions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура действий под готовым сценарием Reels (spec.md §7.3, §10).

    Раскладка:
      [🔄 Переписать]   [🪝 Другой хук]
      [📐 Изменить длину] [💾 Сохранить]
      [🏠 В меню]

    callback_data согласованы с handlers/callbacks.py:
      reel:rewrite / reel:another_hook / reel:length / reel:save / menu
    """
    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Переписать", callback_data="reel:rewrite"),
            InlineKeyboardButton(text="🪝 Другой хук", callback_data="reel:another_hook"),
        ],
        [
            InlineKeyboardButton(text="📐 Изменить длину", callback_data="reel:length"),
            InlineKeyboardButton(text="💾 Сохранить", callback_data="reel:save"),
        ],
        [
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def reel_length_keyboard(current: int | None = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора длины Reels (15/30/60 сек) + возврат.

    У текущей выбранной длины отображается галочка ✓.
    callback_data: reel:len:15 / reel:len:30 / reel:len:60 / reel:back.
    """
    builder = InlineKeyboardBuilder()
    for sec in SUPPORTED_DURATIONS:
        prefix = "✓ " if sec == current else ""
        builder.button(text=f"{prefix}{sec} сек", callback_data=f"reel:len:{sec}")
    builder.button(text="← Назад", callback_data="reel:back")
    builder.adjust(len(SUPPORTED_DURATIONS), 1)
    return builder.as_markup()


def drafts_list_keyboard(drafts: list[dict]) -> InlineKeyboardMarkup:
    """Список черновиков (Фаза 8): по кнопке на черновик + «В меню».

    Каждая кнопка: эмодзи типа (📝 пост / 🎬 reels) + тема (обрезанная).
    callback_data: draft:open:<id>.
    """
    builder = InlineKeyboardBuilder()
    for d in drafts:
        emoji = "🎬" if d.get("content_type") == "reel" else "📝"
        topic = (d.get("topic") or "без темы").strip()
        if len(topic) > 40:
            topic = topic[:39] + "…"
        builder.button(text=f"{emoji} {topic}", callback_data=f"draft:open:{d['id']}")
    builder.button(text="🏠 В меню", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def draft_view_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под открытым черновиком: назад к списку и в меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="← К списку", callback_data="menu:drafts")
    builder.button(text="🏠 В меню", callback_data="menu")
    builder.adjust(2)
    return builder.as_markup()


def profile_view_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под профилем: изменить или в меню (Фаза 8, §10)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить профиль", callback_data="profile:edit")
    builder.button(text="🏠 В меню", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def profile_fields_keyboard() -> InlineKeyboardMarkup:
    """Выбор поля профиля для редактирования (Фаза 8).

    callback_data: profile:field:<key>, где key ∈ niche/audience/tone/
    forbidden/refs; «← Назад» возвращает к просмотру профиля (menu:profile).
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="Ниша", callback_data="profile:field:niche")
    builder.button(text="Аудитория", callback_data="profile:field:audience")
    builder.button(text="Тон", callback_data="profile:field:tone")
    builder.button(text="Запрещённые слова", callback_data="profile:field:forbidden")
    builder.button(text="Референсы", callback_data="profile:field:refs")
    builder.button(text="← Назад", callback_data="menu:profile")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню (spec.md §10).

    Раскладка:
      [✍️ Написать пост]  [🎬 Сценарий Reels]
      [💾 Черновики]      [⚙️ Изменить профиль]

    Используется после онбординга и в Фазах 6/8.
    """
    keyboard = [
        [
            InlineKeyboardButton(text="✍️ Написать пост", callback_data="menu:post"),
            InlineKeyboardButton(text="🎬 Сценарий Reels", callback_data="menu:reel"),
        ],
        [
            InlineKeyboardButton(text="💾 Черновики", callback_data="menu:drafts"),
            InlineKeyboardButton(text="⚙️ Изменить профиль", callback_data="menu:profile"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
