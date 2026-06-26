import json
from pathlib import Path

import aiofiles
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.db import Database
from bot.keyboards.menus import (
    campaign_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    recipients_keyboard,
)
from bot.services.broadcast_service import BroadcastService
from bot.states import CampaignStates
from bot.utils.parsers import parse_usernames, parse_usernames_from_csv

router = Router()
MEDIA_DIR = Path("data/media")


async def send_campaign_view(
    target: Message | CallbackQuery,
    db: Database,
    campaign: dict,
) -> None:
    usernames = json.loads(campaign["usernames_json"])
    media_info = "не прикреплено"
    if campaign["media_path"]:
        media_info = f"{campaign['media_type']} — {Path(campaign['media_path']).name}"

    text = (
        f"📨 Рассылка #{campaign['id']}\n"
        f"Статус: {_status_label(campaign['status'])}\n\n"
        f"👥 Получателей: {len(usernames)}\n"
        f"✏️ Текст: {campaign['message_text'][:100] or '(пусто)'}"
        f"{'...' if len(campaign['message_text'] or '') > 100 else ''}\n"
        f"📎 Медиа: {media_info}\n"
        f"⏱ Задержка: {campaign['delay_seconds']} сек.\n\n"
        f"✅ Отправлено: {campaign['sent_count']} | "
        f"❌ Ошибки: {campaign['error_count']} | "
        f"🚫 Недоставлено: {campaign['failed_count']}"
    )
    markup = campaign_keyboard(campaign["id"], campaign["status"])

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


def _status_label(status: str) -> str:
    labels = {
        "draft": "📝 Черновик",
        "running": "▶️ Запущена",
        "paused": "⏸ Пауза",
        "stopped": "⏹ Остановлена",
        "completed": "✅ Завершена",
    }
    return labels.get(status, status)


@router.message(F.text == "📨 Новая рассылка")
async def new_campaign(message: Message, db: Database, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if not user or not await db.user_has_accounts(message.from_user.id):
        await message.answer("Сначала войдите в аккаунт через «🔑 Вход».")
        return

    active = await db.get_active_campaign(message.from_user.id)
    if active and active["status"] in ("draft", "running", "paused", "stopped"):
        await message.answer(
            "У вас уже есть активная рассылка. Управляйте ей через «📋 Текущая рассылка» "
            "или остановите текущую перед созданием новой."
        )
        return

    campaign_id = await db.create_campaign(message.from_user.id)
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await send_campaign_view(message, db, campaign)


@router.callback_query(F.data.startswith("camp:"))
async def campaign_callbacks(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
    broadcast: BroadcastService,
) -> None:
    _, campaign_id_raw, action = callback.data.split(":", 2)
    campaign_id = int(campaign_id_raw)
    campaign = await db.get_campaign(campaign_id, callback.from_user.id)
    if not campaign:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    if action == "back":
        await send_campaign_view(callback, db, campaign)
        return

    if action == "recipients":
        await callback.message.edit_text(
            "Выберите способ загрузки списка @username:",
            reply_markup=recipients_keyboard(campaign_id),
        )
        await callback.answer()
        return

    if action == "recipients_manual":
        await state.set_state(CampaignStates.waiting_usernames_text)
        await state.update_data(campaign_id=campaign_id, input_mode="manual")
        await callback.message.answer(
            "Отправьте список @username (каждый с новой строки или через запятую):\n"
            "Пример:\n@user1\n@user2\n@user3",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if action == "recipients_file":
        await state.set_state(CampaignStates.waiting_usernames_text)
        await state.update_data(campaign_id=campaign_id, input_mode="file")
        await callback.message.answer(
            "Отправьте файл TXT или CSV со списком @username.",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if action == "text":
        await state.set_state(CampaignStates.waiting_message_text)
        await state.update_data(campaign_id=campaign_id)
        await callback.message.answer(
            "Введите текст сообщения для рассылки:",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if action == "media":
        await state.set_state(CampaignStates.waiting_media)
        await state.update_data(campaign_id=campaign_id)
        await callback.message.answer(
            "Отправьте фото, видео или документ для прикрепления к сообщению.\n"
            "Или отправьте «-» чтобы убрать медиа.",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if action == "delay":
        await state.set_state(CampaignStates.waiting_delay)
        await state.update_data(campaign_id=campaign_id)
        await callback.message.answer(
            "Введите задержку между отправками в секундах (минимум 1):",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if action == "start":
        error = await broadcast.start(callback.from_user.id, campaign_id)
        if error:
            await callback.answer(error, show_alert=True)
            return
        campaign = await db.get_campaign(campaign_id, callback.from_user.id)
        await send_campaign_view(callback, db, campaign)
        await callback.message.answer("▶️ Рассылка запущена!")
        return

    if action == "pause":
        error = await broadcast.pause(callback.from_user.id, campaign_id)
        if error:
            await callback.answer(error, show_alert=True)
            return
        campaign = await db.get_campaign(campaign_id, callback.from_user.id)
        await send_campaign_view(callback, db, campaign)
        return

    if action == "resume":
        error = await broadcast.start(callback.from_user.id, campaign_id)
        if error:
            await callback.answer(error, show_alert=True)
            return
        campaign = await db.get_campaign(campaign_id, callback.from_user.id)
        await send_campaign_view(callback, db, campaign)
        await callback.message.answer("🔄 Рассылка возобновлена!")
        return

    if action == "stop":
        error = await broadcast.stop(callback.from_user.id, campaign_id)
        if error:
            await callback.answer(error, show_alert=True)
            return
        campaign = await db.get_campaign(campaign_id, callback.from_user.id)
        await send_campaign_view(callback, db, campaign)
        await callback.message.answer("⏹ Рассылка остановлена.")
        return

    if action == "stats":
        usernames = json.loads(campaign["usernames_json"])
        errors = json.loads(campaign["errors_json"])
        text = (
            f"📊 Статистика рассылки #{campaign_id}\n\n"
            f"Всего: {len(usernames)}\n"
            f"Обработано: {campaign['current_index']}\n"
            f"✅ Отправлено: {campaign['sent_count']}\n"
            f"❌ Ошибки: {campaign['error_count']}\n"
            f"🚫 Недоставлено: {campaign['failed_count']}\n"
        )
        if errors:
            text += "\nПоследние ошибки:\n"
            for item in errors[-10:]:
                text += f"• @{item['username']}: {item['error']}\n"
        await callback.message.answer(text)
        await callback.answer()
        return


@router.message(CampaignStates.waiting_usernames_text, F.text == "❌ Отмена")
@router.message(CampaignStates.waiting_message_text, F.text == "❌ Отмена")
@router.message(CampaignStates.waiting_media, F.text == "❌ Отмена")
@router.message(CampaignStates.waiting_delay, F.text == "❌ Отмена")
async def cancel_campaign_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    await state.clear()
    user = await db.get_user(message.from_user.id)
    is_authorized = bool(user and user["is_authorized"])
    await message.answer("Отменено.", reply_markup=main_menu_keyboard(is_authorized))
    if campaign_id:
        campaign = await db.get_campaign(campaign_id, message.from_user.id)
        if campaign:
            await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_usernames_text, F.document)
async def process_usernames_file(
    message: Message, state: FSMContext, db: Database, bot
) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    doc = message.document
    filename = (doc.file_name or "").lower()
    if not filename.endswith((".txt", ".csv")):
        await message.answer("Поддерживаются только файлы TXT и CSV.")
        return

    file = await bot.get_file(doc.file_id)
    content_bytes = await bot.download_file(file.file_path)
    content = content_bytes.read().decode("utf-8", errors="ignore")

    if filename.endswith(".csv"):
        usernames = parse_usernames_from_csv(content)
    else:
        usernames = parse_usernames(content)

    if not usernames:
        await message.answer("В файле не найдено ни одного @username.")
        return

    await db.set_campaign_usernames(campaign_id, usernames)
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer(f"✅ Загружено {len(usernames)} получателей.")
    await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_usernames_text, F.text)
async def process_usernames_text(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    usernames = parse_usernames(message.text or "")
    if not usernames:
        await message.answer("Не найдено ни одного @username. Попробуйте снова.")
        return

    await db.set_campaign_usernames(campaign_id, usernames)
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer(f"✅ Добавлено {len(usernames)} получателей.")
    await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_message_text)
async def process_message_text(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    await db.update_campaign(campaign_id, message_text=message.text or "")
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer("✅ Текст сообщения сохранён.")
    await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_media, F.text == "-")
async def remove_media(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    await db.update_campaign(campaign_id, media_path=None, media_type=None)
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer("Медиа удалено.")
    await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_media, F.photo)
async def process_photo(message: Message, state: FSMContext, db: Database, bot) -> None:
    await _save_media(message, state, db, bot, "photo", message.photo[-1].file_id)


@router.message(CampaignStates.waiting_media, F.video)
async def process_video(message: Message, state: FSMContext, db: Database, bot) -> None:
    await _save_media(message, state, db, bot, "video", message.video.file_id)


@router.message(CampaignStates.waiting_media, F.document)
async def process_document(message: Message, state: FSMContext, db: Database, bot) -> None:
    await _save_media(message, state, db, bot, "document", message.document.file_id)


async def _save_media(
    message: Message,
    state: FSMContext,
    db: Database,
    bot,
    media_type: str,
    file_id: str,
) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = {"photo": "jpg", "video": "mp4", "document": "bin"}[media_type]
    dest = MEDIA_DIR / f"campaign_{campaign_id}.{ext}"

    tg_file = await bot.get_file(file_id)
    downloaded = await bot.download_file(tg_file.file_path)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(downloaded.read())

    await db.update_campaign(
        campaign_id,
        media_path=str(dest),
        media_type=media_type,
    )
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer(f"✅ Медиа ({media_type}) прикреплено.")
    await send_campaign_view(message, db, campaign)


@router.message(CampaignStates.waiting_delay)
async def process_delay(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        return

    try:
        delay = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("Введите число, например: 3 или 5.5")
        return

    if delay < 1:
        await message.answer("Минимальная задержка — 1 секунда.")
        return

    await db.update_campaign(campaign_id, delay_seconds=delay)
    await state.clear()
    campaign = await db.get_campaign(campaign_id, message.from_user.id)
    await message.answer(f"✅ Задержка установлена: {delay} сек.")
    await send_campaign_view(message, db, campaign)
