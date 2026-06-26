import os
from dataclasses import dataclass
from urllib.parse import quote

from dotenv import load_dotenv
from python_socks import ProxyType

from bot.constants import (
    PROXY_HOST,
    PROXY_PASSWORD,
    PROXY_PORT,
    PROXY_TELETHON,
    PROXY_TYPE,
    PROXY_USER,
)

load_dotenv()


@dataclass(frozen=True)
class ProxyConfig:
    type: str
    host: str
    port: int
    user: str
    password: str
    use_for_telethon: bool = True
    enabled: bool = True

    @property
    def is_active(self) -> bool:
        return self.enabled and bool(self.host and self.port)

    @property
    def url(self) -> str:
        auth = f"{quote(self.user)}:{quote(self.password)}@"
        return f"{self.type}://{auth}{self.host}:{self.port}"

    def telethon_proxy(self) -> dict | tuple | None:
        if not self.is_active or not self.use_for_telethon:
            return None
        proxy_type = {
            "socks5": ProxyType.SOCKS5,
            "socks4": ProxyType.SOCKS4,
            "http": ProxyType.HTTP,
        }.get(self.type.lower(), ProxyType.SOCKS5)
        return {
            "proxy_type": proxy_type,
            "addr": self.host,
            "port": self.port,
            "username": self.user,
            "password": self.password,
            "rdns": True,
        }


def get_proxy_config() -> ProxyConfig:
    enabled = os.getenv("PROXY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    return ProxyConfig(
        type=PROXY_TYPE,
        host=PROXY_HOST,
        port=PROXY_PORT,
        user=PROXY_USER,
        password=PROXY_PASSWORD,
        use_for_telethon=PROXY_TELETHON,
        enabled=enabled,
    )
