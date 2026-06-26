import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    UserPrivacyRestrictedError,
)

from bot.config import Settings
from bot.constants import TELEGRAM_API_HASH, TELEGRAM_API_ID
from bot.database.db import Database
from bot.proxy import get_proxy_config


class UserClientManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._proxy = get_proxy_config().telethon_proxy()
        self._clients: dict[int, TelegramClient] = {}
        self._pending: dict[int, TelegramClient] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, account_id: int) -> asyncio.Lock:
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    def _create_client(self, session: str | Path, use_proxy: bool = True) -> TelegramClient:
        proxy = self._proxy if use_proxy else None
        return TelegramClient(
            str(session),
            TELEGRAM_API_ID,
            TELEGRAM_API_HASH,
            proxy=proxy,
            connection_retries=10,
            retry_delay=3,
            timeout=30,
            request_retries=5,
            use_ipv6=False,
        )

    def session_path(self, session_name: str) -> Path:
        return self.settings.sessions_dir / session_name

    def _clear_session_files(self, session_name: str) -> None:
        base = self.session_path(session_name)
        for path in base.parent.glob(f"{base.name}*"):
            try:
                path.unlink()
            except OSError:
                pass

    async def _release_account(self, account_id: int) -> None:
        pending = self._pending.pop(account_id, None)
        if pending:
            try:
                await pending.disconnect()
            except Exception:
                pass

        client = self._clients.pop(account_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

        await asyncio.sleep(0.2)

    async def get_client(self, user_id: int) -> TelegramClient | None:
        account = await self.db.get_active_account(user_id)
        if not account:
            return None
        return await self.get_client_by_account(account["id"])

    async def get_client_by_account(self, account_id: int) -> TelegramClient | None:
        account = await self.db.get_account(account_id)
        if not account:
            return None

        async with self._lock(account_id):
            if account_id in self._clients:
                client = self._clients[account_id]
                if not client.is_connected():
                    await client.connect()
                if await client.is_user_authorized():
                    return client
                return None

            session = self.session_path(account["session_name"])
            if not session.with_suffix(".session").exists():
                return None

            client = self._create_client(session)
            await client.connect()
            if await client.is_user_authorized():
                self._clients[account_id] = client
                return client

            await client.disconnect()
            return None

    async def start_auth(self, account_id: int, phone: str) -> None:
        account = await self.db.get_account(account_id)
        if not account:
            raise RuntimeError("Аккаунт не найден")

        async with self._lock(account_id):
            await self._release_account(account_id)
            self._clear_session_files(account["session_name"])

            last_error: Exception | None = None
            session = self.session_path(account["session_name"])
            for use_proxy in (True, False):
                if use_proxy and not self._proxy:
                    continue
                client = self._create_client(session, use_proxy=use_proxy)
                try:
                    await client.connect()
                    await client.send_code_request(phone)
                    self._pending[account_id] = client
                    return
                except Exception as exc:
                    last_error = exc
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            raise last_error or RuntimeError("Не удалось подключиться к Telegram")

    async def complete_auth(
        self, account_id: int, phone: str, code: str = "", password: str | None = None
    ) -> str | None:
        async with self._lock(account_id):
            client = self._pending.get(account_id)
            if not client:
                return "Сессия авторизации не найдена. Начните заново."

            try:
                if password:
                    await client.sign_in(password=password)
                else:
                    await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                if not password:
                    return "2FA_PASSWORD"
                await client.sign_in(password=password)
            except Exception as exc:
                return str(exc)

            self._clients[account_id] = client
            self._pending.pop(account_id, None)

            try:
                me = await client.get_me()
                await self.db.update_account(
                    account_id,
                    phone=phone,
                    tg_user_id=me.id,
                    tg_username=me.username or "",
                    tg_first_name=me.first_name or "",
                )
            except Exception:
                await self.db.update_account(account_id, phone=phone)

            return None

    async def cleanup_unfinished_account(self, account_id: int) -> None:
        account = await self.db.get_account(account_id)
        if not account:
            return
        await self.disconnect_pending(account_id)
        self._clear_session_files(account["session_name"])

    async def disconnect_pending(self, account_id: int) -> None:
        async with self._lock(account_id):
            await self._release_account(account_id)

    async def logout_account(self, account_id: int) -> None:
        account = await self.db.get_account(account_id)
        if not account:
            return

        async with self._lock(account_id):
            pending = self._pending.pop(account_id, None)
            if pending:
                try:
                    await pending.log_out()
                    await pending.disconnect()
                except Exception:
                    pass

            client = self._clients.pop(account_id, None)
            if client:
                try:
                    await client.log_out()
                    await client.disconnect()
                except Exception:
                    pass

            self._clear_session_files(account["session_name"])

    async def switch_account(self, user_id: int, account_id: int) -> None:
        await self.db.set_active_account(user_id, account_id)

    async def send_message(
        self,
        user_id: int,
        username: str,
        text: str,
        media_path: Path | None = None,
        media_type: str | None = None,
    ) -> tuple[bool, str | None]:
        client = await self.get_client(user_id)
        if not client:
            return False, "Аккаунт не авторизован"

        try:
            entity = await client.get_entity(username)
            if media_path and media_path.exists() and media_type:
                await self._send_with_media(client, entity, text, media_path, media_type)
            else:
                await client.send_message(entity, text or " ")
            return True, None
        except FloodWaitError as exc:
            return False, f"FloodWait: подождите {exc.seconds} сек."
        except PeerFloodError:
            return False, "PeerFlood: Telegram ограничил отправку сообщений"
        except UserPrivacyRestrictedError:
            return False, "Пользователь запретил личные сообщения"
        except ValueError:
            return False, "Пользователь не найден"
        except Exception as exc:
            return False, str(exc)

    async def _send_with_media(
        self,
        client: TelegramClient,
        entity: object,
        text: str,
        media_path: Path,
        media_type: str,
    ) -> None:
        if media_type == "photo":
            await client.send_file(entity, media_path, caption=text or None)
        elif media_type == "video":
            await client.send_file(
                entity, media_path, caption=text or None, supports_streaming=True
            )
        elif media_type == "document":
            await client.send_file(entity, media_path, caption=text or None, force_document=True)
        else:
            await client.send_message(entity, text or " ")
