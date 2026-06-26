from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.db import Database
from bot.keyboards.menus import (
    ACCOUNTS_BUTTON,
    LOGIN_BUTTON,
    accounts_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
)
from bot.services.user_client import UserClientManager
from bot.states import AuthStates

router = Router()


async def _is_authorized(db: Database, user_id: int) -> bool:
    return await db.user_has_accounts(user_id)


async def _accounts_text(db: Database, user_id: int) -> tuple[str, list[dict], int | None, dict[int, str]]:
    accounts = await db.list_accounts(user_id)
    user = await db.get_user(user_id)
    active_id = user.get("active_account_id") if user else None
    labels = {acc["id"]: await db.account_label(acc) for acc in accounts}

    if not accounts:
        return "У вас нет подключённых аккаунтов.", accounts, active_id, labels

    lines = ["👤 <b>Ваши аккаунты</b>\n"]
    for acc in accounts:
        mark = "✅ " if acc["id"] == active_id else "▫️ "
        lines.append(f"{mark}{labels[acc['id']]}")
    lines.append("\nНажмите на аккаунт для переключения.")
    return "\n".join(lines), accounts, active_id, labels


async def show_accounts_menu(target: Message | CallbackQuery, db: Database) -> None:
    user_id = target.from_user.id
    text, accounts, active_id, labels = await _accounts_text(db, user_id)
    markup = accounts_keyboard(accounts, active_id, labels)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


async def begin_login(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    account_id = await db.create_pending_account(message.from_user.id)
    await state.set_state(AuthStates.waiting_phone)
    await state.update_data(account_id=account_id)
    await message.answer(
        "🔑 <b>Вход в аккаунт</b>\n\n"
        "<b>Шаг 1 из 3</b> — номер телефона\n\n"
        "Введите номер в международном формате:\n"
        "Например: <code>+79001234567</code>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == LOGIN_BUTTON)
async def start_auth_button(message: Message, state: FSMContext, db: Database) -> None:
    if await _is_authorized(db, message.from_user.id):
        await message.answer(
            "У вас уже есть аккаунты. Управляйте ими через «👤 Аккаунты» "
            "или добавьте новый там же.",
            reply_markup=main_menu_keyboard(True),
        )
        return
    await begin_login(message, state, db)


@router.message(F.text == ACCOUNTS_BUTTON)
async def accounts_menu(message: Message, db: Database) -> None:
    if not await _is_authorized(db, message.from_user.id):
        await message.answer(
            "Сначала войдите через «🔑 Вход».",
            reply_markup=main_menu_keyboard(False),
        )
        return
    await show_accounts_menu(message, db)


@router.callback_query(F.data == "auth:login")
async def start_auth_callback(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if await _is_authorized(db, callback.from_user.id):
        await callback.answer("Используйте «👤 Аккаунты»", show_alert=True)
        return
    await callback.answer()
    await begin_login(callback.message, state, db)


@router.callback_query(F.data == "acc:add")
async def add_account(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await callback.answer()
    await begin_login(callback.message, state, db)


@router.callback_query(F.data.startswith("acc:switch:"))
async def switch_account(
    callback: CallbackQuery,
    db: Database,
    user_clients: UserClientManager,
) -> None:
    account_id = int(callback.data.split(":")[2])
    account = await db.get_account(account_id, callback.from_user.id)
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    client = await user_clients.get_client_by_account(account_id)
    if not client:
        await callback.answer("Сессия аккаунта недействительна", show_alert=True)
        return

    await user_clients.switch_account(callback.from_user.id, account_id)
    label = await db.account_label(account)
    await callback.answer(f"Активен: {label}")
    await show_accounts_menu(callback, db)


@router.callback_query(F.data == "acc:delete_active")
async def delete_active_account(
    callback: CallbackQuery,
    db: Database,
    user_clients: UserClientManager,
) -> None:
    user = await db.get_user(callback.from_user.id)
    account_id = user.get("active_account_id") if user else None
    if not account_id:
        await callback.answer("Нет активного аккаунта", show_alert=True)
        return

    account = await db.get_account(account_id, callback.from_user.id)
    await user_clients.logout_account(account_id)
    await db.delete_account(account_id, callback.from_user.id)

    if await _is_authorized(db, callback.from_user.id):
        await callback.answer("Аккаунт удалён")
        await show_accounts_menu(callback, db)
    else:
        await callback.message.edit_text("Все аккаунты удалены.")
        await callback.message.answer(
            "Войдите снова через «🔑 Вход».",
            reply_markup=main_menu_keyboard(False),
        )
        await callback.answer()


@router.callback_query(F.data == "acc:logout_all")
async def logout_all(
    callback: CallbackQuery,
    db: Database,
    user_clients: UserClientManager,
    state: FSMContext,
) -> None:
    accounts = await db.list_accounts(callback.from_user.id)
    for account in accounts:
        await user_clients.logout_account(account["id"])
    await db.delete_all_accounts(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text("Вы вышли из всех аккаунтов.")
    await callback.message.answer(
        "Для работы снова нажмите «🔑 Вход».",
        reply_markup=main_menu_keyboard(False),
    )
    await callback.answer()


@router.message(AuthStates.waiting_phone, F.text == "❌ Отмена")
@router.message(AuthStates.waiting_code, F.text == "❌ Отмена")
@router.message(AuthStates.waiting_password, F.text == "❌ Отмена")
async def cancel_auth(
    message: Message,
    state: FSMContext,
    user_clients: UserClientManager,
    db: Database,
) -> None:
    data = await state.get_data()
    account_id = data.get("account_id")
    if account_id:
        account = await db.get_account(account_id, message.from_user.id)
        if account and not account.get("tg_user_id"):
            await user_clients.cleanup_unfinished_account(account_id)
            await db.delete_account(account_id, message.from_user.id)

    await state.clear()
    authorized = await _is_authorized(db, message.from_user.id)
    await message.answer(
        "Вход отменён.",
        reply_markup=main_menu_keyboard(authorized),
    )


@router.message(AuthStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
    db: Database,
    user_clients: UserClientManager,
) -> None:
    phone = (message.text or "").strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+") or len(phone) < 8:
        await message.answer(
            "Неверный формат. Введите номер, например: <code>+79001234567</code>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id:
        await state.clear()
        await message.answer("Сессия истекла. Начните заново.")
        return

    try:
        await user_clients.start_auth(account_id, phone)
    except Exception as exc:
        err = str(exc)
        if "database is locked" in err.lower():
            hint = "Повторите вход через 5 секунд."
        elif "connection to telegram failed" in err.lower():
            hint = "Telegram недоступен. Проверьте сеть или VPN."
        else:
            hint = "Попробуйте ещё раз."
        await message.answer(f"Не удалось отправить код: {exc}\n\n{hint}")
        return

    await db.ensure_user(message.from_user.id)
    await db.update_account(account_id, phone=phone)
    await state.update_data(phone=phone)
    await state.set_state(AuthStates.waiting_code)
    await message.answer(
        "🔑 <b>Вход в аккаунт</b>\n\n"
        "<b>Шаг 2 из 3</b> — код подтверждения\n\n"
        "Код отправлен в Telegram (личные сообщения или SMS).\n"
        "Введите полученный код:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(AuthStates.waiting_code)
async def process_code(
    message: Message,
    state: FSMContext,
    db: Database,
    user_clients: UserClientManager,
) -> None:
    code = (message.text or "").strip().replace(" ", "").replace("-", "")
    data = await state.get_data()
    phone = data.get("phone")
    account_id = data.get("account_id")
    if not phone or not account_id:
        await state.clear()
        await message.answer("Сессия истекла. Начните заново.")
        return

    error = await user_clients.complete_auth(account_id, phone, code)
    if error == "2FA_PASSWORD":
        await state.set_state(AuthStates.waiting_password)
        await message.answer(
            "🔑 <b>Вход в аккаунт</b>\n\n"
            "<b>Шаг 3 из 3</b> — пароль 2FA\n\n"
            "Введите пароль облачного пароля Telegram:",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    if error:
        await message.answer(f"Ошибка входа: {error}")
        return

    await db.set_active_account(message.from_user.id, account_id)
    await db.sync_user_authorized(message.from_user.id)
    await state.clear()

    account = await db.get_account(account_id, message.from_user.id)
    label = await db.account_label(account) if account else "аккаунт"
    await message.answer(
        f"✅ Вход выполнен!\nАктивный аккаунт: <b>{label}</b>\n\n"
        "Рассылки идут от активного аккаунта. Сменить — «👤 Аккаунты».",
        reply_markup=main_menu_keyboard(True),
        parse_mode="HTML",
    )


@router.message(AuthStates.waiting_password)
async def process_password(
    message: Message,
    state: FSMContext,
    db: Database,
    user_clients: UserClientManager,
) -> None:
    password = message.text or ""
    data = await state.get_data()
    phone = data.get("phone")
    account_id = data.get("account_id")
    if not phone or not account_id:
        await state.clear()
        await message.answer("Сессия истекла. Начните заново.")
        return

    error = await user_clients.complete_auth(account_id, phone, password=password)
    if error:
        await message.answer(f"Ошибка входа: {error}")
        return

    await db.set_active_account(message.from_user.id, account_id)
    await db.sync_user_authorized(message.from_user.id)
    await state.clear()

    account = await db.get_account(account_id, message.from_user.id)
    label = await db.account_label(account) if account else "аккаунт"
    await message.answer(
        f"✅ Вход выполнен!\nАктивный аккаунт: <b>{label}</b>\n\n"
        "Рассылки идут от активного аккаунта. Сменить — «👤 Аккаунты».",
        reply_markup=main_menu_keyboard(True),
        parse_mode="HTML",
    )
