import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings
from bot.database.db import Database
from bot.handlers import auth, campaign, common
from bot.proxy import get_proxy_config
from bot.services.broadcast_service import BroadcastService
from bot.services.user_client import UserClientManager


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = get_settings()
    db = Database(settings.database_path)
    await db.connect()

    proxy = get_proxy_config()
    session = AiohttpSession(proxy=proxy.url) if proxy.is_active else AiohttpSession()
    if proxy.is_active:
        logging.info("Bot API proxy: %s://%s:%s", proxy.type, proxy.host, proxy.port)
    else:
        logging.info("Bot API: прямое подключение (без прокси)")

    bot = Bot(token=settings.bot_token, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    user_clients = UserClientManager(settings, db)
    broadcast = BroadcastService(db, user_clients, bot)

    dp["db"] = db
    dp["user_clients"] = user_clients
    dp["broadcast"] = broadcast

    dp.include_router(common.router)
    dp.include_router(auth.router)
    dp.include_router(campaign.router)

    try:
        while True:
            try:
                await dp.start_polling(bot)
                break
            except TelegramNetworkError as exc:
                logging.warning("Нет связи с Telegram API: %s. Повтор через 15 сек...", exc)
                await asyncio.sleep(15)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
