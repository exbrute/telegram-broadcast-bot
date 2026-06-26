import asyncio
import json
from pathlib import Path

from aiogram import Bot

from bot.database.db import Database
from bot.services.user_client import UserClientManager


class BroadcastService:
    def __init__(
        self,
        db: Database,
        user_clients: UserClientManager,
        bot: Bot,
    ) -> None:
        self.db = db
        self.user_clients = user_clients
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}
        self._stop_flags: dict[int, asyncio.Event] = {}

    def is_running(self, user_id: int) -> bool:
        task = self._tasks.get(user_id)
        return task is not None and not task.done()

    async def start(self, user_id: int, campaign_id: int) -> str | None:
        if self.is_running(user_id):
            return "Рассылка уже выполняется"

        campaign = await self.db.get_campaign(campaign_id, user_id)
        if not campaign:
            return "Кампания не найдена"

        usernames = json.loads(campaign["usernames_json"])
        if not usernames:
            return "Список получателей пуст"

        if not campaign["message_text"] and not campaign["media_path"]:
            return "Укажите текст сообщения или прикрепите медиа"

        client = await self.user_clients.get_client(user_id)
        if not client:
            return "Сначала авторизуйте Telegram-аккаунт"

        stop_event = asyncio.Event()
        self._stop_flags[user_id] = stop_event
        await self.db.update_campaign(campaign_id, status="running")

        task = asyncio.create_task(
            self._run_broadcast(user_id, campaign_id, stop_event)
        )
        self._tasks[user_id] = task
        return None

    async def pause(self, user_id: int, campaign_id: int) -> str | None:
        if not self.is_running(user_id):
            return "Рассылка не запущена"

        self._stop_flags[user_id].set()
        await self.db.update_campaign(campaign_id, status="paused")
        return None

    async def stop(self, user_id: int, campaign_id: int) -> str | None:
        if self.is_running(user_id):
            self._stop_flags[user_id].set()

        await self.db.update_campaign(
            campaign_id,
            status="stopped",
        )
        return None

    async def _run_broadcast(
        self, user_id: int, campaign_id: int, stop_event: asyncio.Event
    ) -> None:
        try:
            while True:
                campaign = await self.db.get_campaign(campaign_id, user_id)
                if not campaign or campaign["status"] not in ("running",):
                    break

                usernames = json.loads(campaign["usernames_json"])
                index = campaign["current_index"]
                delay = float(campaign["delay_seconds"])
                text = campaign["message_text"] or ""
                media_path = (
                    Path(campaign["media_path"]) if campaign["media_path"] else None
                )
                media_type = campaign["media_type"]

                if index >= len(usernames):
                    await self.db.update_campaign(campaign_id, status="completed")
                    await self._notify(
                        user_id,
                        "✅ Рассылка завершена!\n\n" + await self._stats_text(campaign_id, user_id),
                    )
                    break

                username = usernames[index]
                success, error = await self.user_clients.send_message(
                    user_id,
                    username,
                    text,
                    media_path=media_path,
                    media_type=media_type,
                )

                if success:
                    await self.db.increment_campaign_stats(
                        campaign_id,
                        sent=1,
                        current_index=index + 1,
                    )
                else:
                    await self.db.append_campaign_error(campaign_id, username, error or "Ошибка")
                    is_flood = error and ("FloodWait" in error or "PeerFlood" in error)
                    if is_flood:
                        await self.db.increment_campaign_stats(
                            campaign_id,
                            failed=1,
                            current_index=index + 1,
                        )
                    else:
                        await self.db.increment_campaign_stats(
                            campaign_id,
                            errors=1,
                            current_index=index + 1,
                        )

                campaign = await self.db.get_campaign(campaign_id, user_id)
                if campaign and campaign["current_index"] % 10 == 0:
                    await self._notify_progress(user_id, campaign_id)

                if stop_event.is_set():
                    await self.db.update_campaign(campaign_id, status="paused")
                    await self._notify(
                        user_id,
                        "⏸ Рассылка приостановлена.\n\n"
                        + await self._stats_text(campaign_id, user_id),
                    )
                    break

                if index + 1 < len(usernames):
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            await self.db.update_campaign(campaign_id, status="paused")
            raise
        finally:
            self._tasks.pop(user_id, None)
            self._stop_flags.pop(user_id, None)

    async def _notify_progress(self, user_id: int, campaign_id: int) -> None:
        text = "📊 Прогресс рассылки:\n\n" + await self._stats_text(campaign_id, user_id)
        try:
            await self.bot.send_message(user_id, text)
        except Exception:
            pass

    async def _notify(self, user_id: int, text: str) -> None:
        try:
            await self.bot.send_message(user_id, text)
        except Exception:
            pass

    async def _stats_text(self, campaign_id: int, user_id: int) -> str:
        campaign = await self.db.get_campaign(campaign_id, user_id)
        if not campaign:
            return ""
        usernames = json.loads(campaign["usernames_json"])
        total = len(usernames)
        processed = campaign["current_index"]
        return (
            f"Всего: {total}\n"
            f"Обработано: {processed}\n"
            f"✅ Отправлено: {campaign['sent_count']}\n"
            f"❌ Ошибки: {campaign['error_count']}\n"
            f"🚫 Недоставлено: {campaign['failed_count']}"
        )
