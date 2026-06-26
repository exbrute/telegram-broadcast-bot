import json

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.database.db import Database
from bot.keyboards.menus import login_inline_keyboard, main_menu_keyboard

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    await db.ensure_user(message.from_user.id)
    is_authorized = await db.user_has_accounts(message.from_user.id)
    active = await db.get_active_account(message.from_user.id) if is_authorized else None
    active_label = await db.account_label(active) if active else ""

    await message.answer(
        "👋 Добро пожаловать в бот для рассылки сообщений!\n\n"
        "Бот отправляет личные сообщения от вашего Telegram-аккаунта "
        "пользователям из списка @username.\n\n"
        + (
            f"✅ Активный аккаунт: {active_label}\n"
            "Сменить аккаунт — «👤 Аккаунты»."
            if is_authorized
            else "Нажмите «🔑 Вход», чтобы подключить Telegram-аккаунт."
        ),
        reply_markup=main_menu_keyboard(is_authorized),
    )
    if not is_authorized:
        await message.answer(
            "Для работы бота нужен вход в Telegram-аккаунт:",
            reply_markup=login_inline_keyboard(),
        )


@router.message(F.text == "📋 Текущая рассылка")
async def show_current_campaign(message: Message, db: Database) -> None:
    from bot.handlers.campaign import send_campaign_view

    campaign = await db.get_active_campaign(message.from_user.id)
    if not campaign:
        await message.answer("Нет активной рассылки. Создайте новую через «📨 Новая рассылка».")
        return
    await send_campaign_view(message, db, campaign)


@router.message(F.text == "📊 Статистика")
async def show_stats(message: Message, db: Database) -> None:
    campaign = await db.get_active_campaign(message.from_user.id)
    if not campaign:
        await message.answer("Нет активной рассылки для отображения статистики.")
        return

    usernames = json.loads(campaign["usernames_json"])
    errors = json.loads(campaign["errors_json"])
    text = (
        f"📊 Статистика рассылки #{campaign['id']}\n"
        f"Статус: {_status_label(campaign['status'])}\n\n"
        f"Всего получателей: {len(usernames)}\n"
        f"Обработано: {campaign['current_index']}\n"
        f"✅ Отправлено: {campaign['sent_count']}\n"
        f"❌ Ошибки: {campaign['error_count']}\n"
        f"🚫 Недоставлено: {campaign['failed_count']}\n"
    )
    if errors:
        text += "\nПоследние ошибки:\n"
        for item in errors[-5:]:
            text += f"• @{item['username']}: {item['error']}\n"

    await message.answer(text)


def _status_label(status: str) -> str:
    labels = {
        "draft": "📝 Черновик",
        "running": "▶️ Запущена",
        "paused": "⏸ Пауза",
        "stopped": "⏹ Остановлена",
        "completed": "✅ Завершена",
    }
    return labels.get(status, status)
