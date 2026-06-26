import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.constants import TELEGRAM_API_HASH, TELEGRAM_API_ID

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    database_path: Path
    sessions_dir: Path


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Заполните BOT_TOKEN в файле .env (см. .env.example)")

    database_path = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
    sessions_dir = Path(os.getenv("SESSIONS_DIR", "data/sessions"))

    return Settings(
        bot_token=bot_token,
        api_id=TELEGRAM_API_ID,
        api_hash=TELEGRAM_API_HASH,
        database_path=database_path,
        sessions_dir=sessions_dir,
    )
