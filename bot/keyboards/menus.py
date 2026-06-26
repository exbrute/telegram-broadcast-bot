from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

LOGIN_BUTTON = "🔑 Вход"
ACCOUNTS_BUTTON = "👤 Аккаунты"


def main_menu_keyboard(is_authorized: bool) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    if is_authorized:
        builder.button(text="📨 Новая рассылка")
        builder.button(text="📋 Текущая рассылка")
        builder.button(text="📊 Статистика")
        builder.button(text=ACCOUNTS_BUTTON)
    else:
        builder.button(text=LOGIN_BUTTON)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def login_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=LOGIN_BUTTON, callback_data="auth:login"))
    return builder.as_markup()


def accounts_keyboard(
    accounts: list[dict],
    active_account_id: int | None,
    labels: dict[int, str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for account in accounts:
        acc_id = account["id"]
        label = labels.get(acc_id, f"Аккаунт #{acc_id}")
        prefix = "✅ " if acc_id == active_account_id else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{label}",
                callback_data=f"acc:switch:{acc_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="acc:add"),
    )
    if len(accounts) > 1:
        builder.row(
            InlineKeyboardButton(text="🗑 Удалить текущий", callback_data="acc:delete_active"),
        )
    builder.row(
        InlineKeyboardButton(text="🚪 Выйти из всех", callback_data="acc:logout_all"),
    )
    return builder.as_markup()


def campaign_keyboard(campaign_id: int, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="👥 Список получателей",
            callback_data=f"camp:{campaign_id}:recipients",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Текст сообщения",
            callback_data=f"camp:{campaign_id}:text",
        ),
        InlineKeyboardButton(
            text="📎 Медиа",
            callback_data=f"camp:{campaign_id}:media",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⏱ Задержка",
            callback_data=f"camp:{campaign_id}:delay",
        )
    )

    if status in ("draft", "paused", "stopped"):
        builder.row(
            InlineKeyboardButton(
                text="▶️ Запустить" if status == "draft" else "🔄 Возобновить",
                callback_data=f"camp:{campaign_id}:start"
                if status == "draft"
                else f"camp:{campaign_id}:resume",
            )
        )
    if status == "running":
        builder.row(
            InlineKeyboardButton(
                text="⏸ Пауза",
                callback_data=f"camp:{campaign_id}:pause",
            )
        )
    if status in ("running", "paused", "draft", "stopped"):
        builder.row(
            InlineKeyboardButton(
                text="⏹ Остановить",
                callback_data=f"camp:{campaign_id}:stop",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data=f"camp:{campaign_id}:stats",
        )
    )
    return builder.as_markup()


def recipients_keyboard(campaign_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📝 Ввести вручную",
            callback_data=f"camp:{campaign_id}:recipients_manual",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📄 Загрузить файл (TXT/CSV)",
            callback_data=f"camp:{campaign_id}:recipients_file",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"camp:{campaign_id}:back",
        )
    )
    return builder.as_markup()


def cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)
