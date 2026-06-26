import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 30000")
        await self._init_schema()
        await self._migrate_legacy_sessions()

    async def close(self) -> None:
        await self._conn.close()

    async def _init_schema(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                phone TEXT,
                is_authorized INTEGER NOT NULL DEFAULT 0,
                active_account_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                tg_user_id INTEGER,
                tg_username TEXT,
                tg_first_name TEXT,
                session_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_text TEXT NOT NULL DEFAULT '',
                media_type TEXT,
                media_path TEXT,
                delay_seconds REAL NOT NULL DEFAULT 3.0,
                usernames_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                current_index INTEGER NOT NULL DEFAULT 0,
                sent_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            );
            """
        )
        await self._conn.commit()
        await self._ensure_column("users", "active_account_id", "INTEGER")

    async def _ensure_column(self, table: str, column: str, col_type: str) -> None:
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if column not in columns:
            await self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
            )
            await self._conn.commit()

    async def _migrate_legacy_sessions(self) -> None:
        cursor = await self._conn.execute(
            "SELECT telegram_id, phone FROM users WHERE is_authorized = 1"
        )
        users = await cursor.fetchall()
        for row in users:
            user_id = row["telegram_id"]
            accounts = await self.list_accounts(user_id)
            if accounts:
                continue
            legacy_name = f"user_{user_id}"
            session_file = Path("data/sessions") / f"{legacy_name}.session"
            if not session_file.exists():
                continue
            account_id = await self.create_account(user_id, row["phone"] or "", legacy_name)
            await self.set_active_account(user_id, account_id)

    async def ensure_user(self, telegram_id: int) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
            (telegram_id,),
        )
        await self._conn.commit()

    async def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def user_has_accounts(self, telegram_id: int) -> bool:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM accounts WHERE user_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return bool(row and row["cnt"] > 0)

    async def sync_user_authorized(self, telegram_id: int) -> None:
        has = await self.user_has_accounts(telegram_id)
        await self._conn.execute(
            "UPDATE users SET is_authorized = ? WHERE telegram_id = ?",
            (1 if has else 0, telegram_id),
        )
        await self._conn.commit()

    async def create_account(self, user_id: int, phone: str, session_name: str) -> int:
        cursor = await self._conn.execute(
            """
            INSERT INTO accounts (user_id, phone, session_name)
            VALUES (?, ?, ?)
            """,
            (user_id, phone, session_name),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def create_pending_account(self, user_id: int) -> int:
        temp_name = f"pending_{user_id}_{time.time_ns()}"
        cursor = await self._conn.execute(
            "INSERT INTO accounts (user_id, phone, session_name) VALUES (?, '', ?)",
            (user_id, temp_name),
        )
        account_id = cursor.lastrowid
        session_name = f"account_{account_id}"
        await self._conn.execute(
            "UPDATE accounts SET session_name = ? WHERE id = ?",
            (session_name, account_id),
        )
        await self._conn.commit()
        return account_id

    async def get_account(self, account_id: int, user_id: int | None = None) -> dict[str, Any] | None:
        if user_id is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM accounts WHERE id = ? AND user_id = ?",
                (account_id, user_id),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?",
                (account_id,),
            )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_accounts(self, user_id: int) -> list[dict[str, Any]]:
        cursor = await self._conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_active_account(self, user_id: int) -> dict[str, Any] | None:
        user = await self.get_user(user_id)
        if not user or not user.get("active_account_id"):
            accounts = await self.list_accounts(user_id)
            if not accounts:
                return None
            await self.set_active_account(user_id, accounts[0]["id"])
            return accounts[0]
        return await self.get_account(user["active_account_id"], user_id)

    async def set_active_account(self, user_id: int, account_id: int) -> None:
        account = await self.get_account(account_id, user_id)
        if not account:
            return
        await self._conn.execute(
            "UPDATE users SET active_account_id = ?, is_authorized = 1 WHERE telegram_id = ?",
            (account_id, user_id),
        )
        await self._conn.commit()

    async def update_account(self, account_id: int, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [account_id]
        await self._conn.execute(
            f"UPDATE accounts SET {columns} WHERE id = ?",
            values,
        )
        await self._conn.commit()

    async def delete_account(self, account_id: int, user_id: int) -> bool:
        account = await self.get_account(account_id, user_id)
        if not account:
            return False
        user = await self.get_user(user_id)
        await self._conn.execute(
            "DELETE FROM accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )
        if user and user.get("active_account_id") == account_id:
            cursor = await self._conn.execute(
                "SELECT id FROM accounts WHERE user_id = ? ORDER BY id ASC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            await self._conn.execute(
                "UPDATE users SET active_account_id = ? WHERE telegram_id = ?",
                (row["id"] if row else None, user_id),
            )
        await self.sync_user_authorized(user_id)
        await self._conn.commit()
        return True

    async def delete_all_accounts(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
        await self._conn.execute(
            "UPDATE users SET active_account_id = NULL, is_authorized = 0 WHERE telegram_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    async def account_label(self, account: dict[str, Any]) -> str:
        if account.get("tg_username"):
            return f"@{account['tg_username']}"
        if account.get("tg_first_name"):
            return account["tg_first_name"]
        if account.get("phone"):
            return account["phone"]
        return f"Аккаунт #{account['id']}"

    async def create_campaign(self, user_id: int) -> int:
        cursor = await self._conn.execute(
            """
            INSERT INTO campaigns (user_id, status)
            VALUES (?, 'draft')
            """,
            (user_id,),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_campaign(self, campaign_id: int, user_id: int) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT * FROM campaigns WHERE id = ? AND user_id = ?",
            (campaign_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_active_campaign(self, user_id: int) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM campaigns
            WHERE user_id = ? AND status IN ('draft', 'running', 'paused', 'stopped')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_campaign(self, campaign_id: int, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields if key != "updated_at")
        columns += ", updated_at = datetime('now')"
        values = [value for key, value in fields.items() if key != "updated_at"]
        values.append(campaign_id)
        await self._conn.execute(
            f"UPDATE campaigns SET {columns} WHERE id = ?",
            values,
        )
        await self._conn.commit()

    async def set_campaign_usernames(self, campaign_id: int, usernames: list[str]) -> None:
        await self.update_campaign(
            campaign_id,
            usernames_json=json.dumps(usernames, ensure_ascii=False),
        )

    async def append_campaign_error(
        self, campaign_id: int, username: str, error: str
    ) -> None:
        campaign = await self._get_campaign_by_id(campaign_id)
        if not campaign:
            return
        errors = json.loads(campaign["errors_json"])
        errors.append({"username": username, "error": error})
        await self.update_campaign(campaign_id, errors_json=json.dumps(errors, ensure_ascii=False))

    async def _get_campaign_by_id(self, campaign_id: int) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (campaign_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def increment_campaign_stats(
        self,
        campaign_id: int,
        *,
        sent: int = 0,
        errors: int = 0,
        failed: int = 0,
        current_index: int | None = None,
    ) -> None:
        campaign = await self._get_campaign_by_id(campaign_id)
        if not campaign:
            return
        fields: dict[str, Any] = {
            "sent_count": campaign["sent_count"] + sent,
            "error_count": campaign["error_count"] + errors,
            "failed_count": campaign["failed_count"] + failed,
        }
        if current_index is not None:
            fields["current_index"] = current_index
        await self.update_campaign(campaign_id, **fields)
