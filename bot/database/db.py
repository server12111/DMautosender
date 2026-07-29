import asyncio
import functools
import logging
import aiosqlite
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta

from ..config import config
from .models import (
    SCHEMA, MIGRATIONS,
    Account, TargetUser, SendLogEntry,
    BotUser, Subscription, PromoCode, Payment, Campaign,
)

logger = logging.getLogger("dmsender.database")


class _ReentrantAsyncLock:
    """Task-reentrant lock used to keep one SQLite transaction at a time."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None
        self._depth = 0

    async def __aenter__(self) -> "_ReentrantAsyncLock":
        task = asyncio.current_task()
        if task is self._owner:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


def _serialized_write(method):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._write_lock:
            try:
                return await method(self, *args, **kwargs)
            except BaseException:
                if self._conn:
                    await self._conn.rollback()
                raise

    return wrapper


class Database:
    def __init__(self, db_path: Path) -> None:
        self._path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = _ReentrantAsyncLock()

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._run_migrations()
        await self._ensure_settings_schema()
        await self._ensure_accounts_schema()

    async def _run_migrations(self) -> None:
        for sql in MIGRATIONS:
            try:
                await self._conn.execute(sql)
                await self._conn.commit()
            except aiosqlite.OperationalError as exc:
                await self._conn.rollback()
                # Migrations are intentionally additive and are retried on
                # every startup. Duplicate-column errors are expected after
                # the first successful run; other schema errors are real.
                if "duplicate column name" in str(exc).lower():
                    logger.debug("Migration already applied: %s", sql)
                    continue
                logger.exception("Database migration failed: %s", sql)
                raise

    async def _ensure_settings_schema(self) -> None:
        """Upgrade legacy UNIQUE(key) settings tables to per-user uniqueness."""
        async with self._conn.execute("PRAGMA index_list(settings)") as cur:
            indexes = await cur.fetchall()

        for index in indexes:
            if not index[2]:
                continue
            index_name = str(index[1]).replace('"', '""')
            async with self._conn.execute(
                f'PRAGMA index_info("{index_name}")'
            ) as cur:
                columns = [row[2] for row in await cur.fetchall()]
            if columns == ["user_id", "key"]:
                return

        try:
            await self._conn.executescript(
                """
                BEGIN IMMEDIATE;
                DROP TABLE IF EXISTS settings_new;
                CREATE TABLE settings_new (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    key     TEXT NOT NULL,
                    value   TEXT NOT NULL,
                    UNIQUE(user_id, key)
                );
                INSERT OR REPLACE INTO settings_new (user_id, key, value)
                    SELECT COALESCE(user_id, 0), key, value
                    FROM settings ORDER BY id;
                DROP TABLE settings;
                ALTER TABLE settings_new RENAME TO settings;
                COMMIT;
                """
            )
        except Exception:
            await self._conn.rollback()
            raise

    async def _ensure_accounts_schema(self) -> None:
        """Upgrade legacy UNIQUE(phone) accounts without changing account IDs."""
        async with self._conn.execute("PRAGMA index_list(accounts)") as cur:
            indexes = await cur.fetchall()
        for index in indexes:
            if not index[2]:
                continue
            index_name = str(index[1]).replace('"', '""')
            async with self._conn.execute(
                f'PRAGMA index_info("{index_name}")'
            ) as cur:
                columns = [row[2] for row in await cur.fetchall()]
            if columns == ["user_id", "phone"]:
                return

        try:
            await self._conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN IMMEDIATE;
                DROP TABLE IF EXISTS accounts_new;
                CREATE TABLE accounts_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL DEFAULT 0,
                    phone        TEXT NOT NULL,
                    name         TEXT,
                    api_id       INTEGER NOT NULL,
                    api_hash     TEXT NOT NULL,
                    session_file TEXT,
                    proxy        TEXT,
                    is_active    INTEGER DEFAULT 1,
                    status       TEXT DEFAULT 'connected',
                    created_at   TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, phone)
                );
                INSERT INTO accounts_new (
                    id, user_id, phone, name, api_id, api_hash, session_file,
                    proxy, is_active, status, created_at
                )
                SELECT
                    id, COALESCE(user_id, 0), phone, name, api_id, api_hash,
                    session_file, proxy, is_active, status, created_at
                FROM accounts;
                DROP TABLE accounts;
                ALTER TABLE accounts_new RENAME TO accounts;
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )
        except Exception:
            await self._conn.rollback()
            await self._conn.execute("PRAGMA foreign_keys=ON")
            raise

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def get_or_create_user(
        self, tg_id: int, username: Optional[str], full_name: Optional[str], referrer_id: Optional[int] = None
    ) -> BotUser:
        async with self._write_lock:
            async with self._conn.execute(
                """INSERT OR IGNORE INTO users
                   (tg_id, username, full_name, referrer_id)
                   VALUES (?, ?, ?, ?)""",
                (tg_id, username, full_name, referrer_id),
            ) as cur:
                created = cur.rowcount == 1

            await self._conn.execute(
                "UPDATE users SET username=?, full_name=? WHERE tg_id=?",
                (username, full_name, tg_id),
            )
            async with self._conn.execute(
                "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
            ) as cur:
                row = await cur.fetchone()

            if created:
                expires = (
                    datetime.utcnow()
                    + timedelta(days=config.DEFAULT_TRIAL_DAYS)
                ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                await self._conn.execute(
                    """INSERT INTO subscriptions
                       (user_id, plan, expires_at, provider)
                       VALUES (?, 'free', ?, 'trial')""",
                    (row["id"], expires),
                )
            await self._conn.commit()
        return _row_to_user(row)

    async def get_user_by_tg_id(self, tg_id: int) -> Optional[BotUser]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: int) -> Optional[BotUser]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    @_serialized_write
    async def set_user_agreed(self, tg_id: int) -> None:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await self._conn.execute(
            "UPDATE users SET agreed_at = ? WHERE tg_id = ?", (now, tg_id)
        )
        await self._conn.commit()

    @_serialized_write
    async def ban_user(self, user_id: int, banned: bool = True) -> None:
        await self._conn.execute(
            "UPDATE users SET is_banned = ? WHERE id = ?", (1 if banned else 0, user_id)
        )
        await self._conn.commit()

    async def get_all_users(self, limit: int = 50, offset: int = 0) -> list[BotUser]:
        async with self._conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_user(r) for r in rows]

    async def count_users(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM users") as cur:
            return (await cur.fetchone())[0]


    @_serialized_write
    async def add_balance(self, user_id: int, amount: float) -> None:
        await self._conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id)
        )
        await self._conn.commit()

    async def get_referrals_count(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)
        ) as cur:
            return (await cur.fetchone())[0]

    async def get_all_tg_ids(self) -> list[int]:
        async with self._conn.execute("SELECT tg_id FROM users WHERE is_banned = 0") as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    # ══════════════════════════════════════════════════════════════════════════
    # SUBSCRIPTIONS
    # ══════════════════════════════════════════════════════════════════════════

    async def get_active_subscription(self, user_id: int) -> Optional[Subscription]:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._conn.execute(
            """SELECT * FROM subscriptions
               WHERE user_id = ? AND is_active = 1
               AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, now),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_sub(row) if row else None

    async def get_subscription_plan(self, user_id: int) -> str:
        """Returns current plan name or 'free'."""
        sub = await self.get_active_subscription(user_id)
        return sub.plan if sub else "free"

    @_serialized_write
    async def create_subscription(
        self,
        user_id: int,
        plan: str,
        duration_days: int,
        provider: str,
        payment_id: Optional[str] = None,
    ) -> Subscription:
        # Deactivate old subscriptions
        await self._conn.execute(
            "UPDATE subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        expires = (
            datetime.utcnow() + timedelta(days=duration_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        async with self._conn.execute(
            """INSERT INTO subscriptions (user_id, plan, expires_at, provider, payment_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, plan, expires, provider, payment_id),
        ) as cur:
            sub_id = cur.lastrowid
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (sub_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_sub(row)

    async def count_active_subscriptions(self) -> int:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE is_active=1 AND (expires_at IS NULL OR expires_at > ?)",
            (now,),
        ) as cur:
            return (await cur.fetchone())[0]

    # ══════════════════════════════════════════════════════════════════════════
    # PROMO CODES
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def create_promo(
        self,
        code: str,
        plan: str,
        duration_days: int,
        max_uses: int,
        created_by: int,
        expires_at: Optional[str] = None,
    ) -> PromoCode:
        async with self._conn.execute(
            """INSERT INTO promo_codes (code, plan, duration_days, max_uses, created_by, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code.upper(), plan, duration_days, max_uses, created_by, expires_at),
        ) as cur:
            promo_id = cur.lastrowid
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT * FROM promo_codes WHERE id = ?", (promo_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_promo(row)

    async def get_promo_by_code(self, code: str) -> Optional[PromoCode]:
        async with self._conn.execute(
            "SELECT * FROM promo_codes WHERE code = ? AND is_active = 1",
            (code.upper(),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_promo(row) if row else None

    async def get_all_promos(self) -> list[PromoCode]:
        async with self._conn.execute(
            "SELECT * FROM promo_codes ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_promo(r) for r in rows]

    @_serialized_write
    async def use_promo(self, promo_id: int, user_id: int) -> bool:
        """Returns False if already used by this user or exhausted."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._write_lock:
            try:
                async with self._conn.execute(
                    """INSERT INTO promo_activations (promo_id, user_id)
                       SELECT ?, ?
                       WHERE EXISTS (
                           SELECT 1 FROM promo_codes
                           WHERE id = ? AND is_active = 1
                             AND (expires_at IS NULL OR expires_at > ?)
                             AND (max_uses < 0 OR used_count < max_uses)
                       )""",
                    (promo_id, user_id, promo_id, now),
                ) as cur:
                    if cur.rowcount != 1:
                        await self._conn.rollback()
                        return False
                await self._conn.execute(
                    "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?",
                    (promo_id,),
                )
                await self._conn.execute(
                    """UPDATE promo_codes SET is_active = 0
                       WHERE id = ? AND max_uses >= 0
                         AND used_count >= max_uses""",
                    (promo_id,),
                )
                await self._conn.commit()
                return True
            except aiosqlite.IntegrityError:
                await self._conn.rollback()
                return False

    @_serialized_write
    async def redeem_promo(
        self, promo_id: int, user_id: int
    ) -> Optional[Subscription]:
        """Atomically consume a promo and grant its subscription."""
        now_dt = datetime.utcnow()
        now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        async with self._write_lock:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                async with self._conn.execute(
                    """SELECT * FROM promo_codes
                       WHERE id=? AND is_active=1
                         AND (expires_at IS NULL OR expires_at > ?)
                         AND (max_uses < 0 OR used_count < max_uses)""",
                    (promo_id, now),
                ) as cur:
                    promo = await cur.fetchone()
                if not promo:
                    await self._conn.rollback()
                    return None

                await self._conn.execute(
                    """INSERT INTO promo_activations (promo_id, user_id)
                       VALUES (?, ?)""",
                    (promo_id, user_id),
                )
                await self._conn.execute(
                    "UPDATE promo_codes SET used_count=used_count+1 WHERE id=?",
                    (promo_id,),
                )
                await self._conn.execute(
                    """UPDATE promo_codes SET is_active=0
                       WHERE id=? AND max_uses >= 0
                         AND used_count >= max_uses""",
                    (promo_id,),
                )
                await self._conn.execute(
                    """UPDATE subscriptions SET is_active=0
                       WHERE user_id=? AND is_active=1""",
                    (user_id,),
                )
                expires = (
                    now_dt + timedelta(days=promo["duration_days"])
                ).strftime("%Y-%m-%d %H:%M:%S")
                async with self._conn.execute(
                    """INSERT INTO subscriptions
                       (user_id, plan, expires_at, provider, payment_id)
                       VALUES (?, ?, ?, 'promo', ?)""",
                    (
                        user_id,
                        promo["plan"],
                        expires,
                        f"promo:{promo['code']}",
                    ),
                ) as cur:
                    subscription_id = cur.lastrowid
                await self._conn.commit()
                async with self._conn.execute(
                    "SELECT * FROM subscriptions WHERE id=?",
                    (subscription_id,),
                ) as cur:
                    subscription = await cur.fetchone()
                return _row_to_sub(subscription)
            except aiosqlite.IntegrityError:
                await self._conn.rollback()
                return None

    @_serialized_write
    async def deactivate_promo(self, promo_id: int) -> None:
        await self._conn.execute(
            "UPDATE promo_codes SET is_active = 0 WHERE id = ?", (promo_id,)
        )
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # PAYMENTS
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def create_payment(
        self,
        user_id: int,
        provider: str,
        plan: str,
        amount: float,
        currency: str = "USD",
        external_id: Optional[str] = None,
    ) -> Payment:
        async with self._conn.execute(
            """INSERT INTO payments (user_id, provider, plan, amount, currency, external_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, provider, plan, amount, currency, external_id),
        ) as cur:
            pay_id = cur.lastrowid
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT * FROM payments WHERE id = ?", (pay_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_payment(row)

    @_serialized_write
    async def update_payment_status(
        self, payment_id: int, status: str, external_id: Optional[str] = None
    ) -> None:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") if status == "paid" else None
        await self._conn.execute(
            "UPDATE payments SET status=?, paid_at=?, external_id=COALESCE(?, external_id) WHERE id=?",
            (status, now, external_id, payment_id),
        )
        await self._conn.commit()

    async def get_payment(self, payment_id: int) -> Optional[Payment]:
        async with self._conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_payment(row) if row else None

    async def get_user_payments(self, user_id: int, limit: int = 10) -> list[Payment]:
        async with self._conn.execute(
            "SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_payment(r) for r in rows]

    async def get_all_payments(self, limit: int = 50) -> list[Payment]:
        async with self._conn.execute(
            "SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_payment(r) for r in rows]

    # ══════════════════════════════════════════════════════════════════════════
    # BOT SETTINGS (global, admin-managed)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_bot_setting(self, key: str, default: str = "") -> str:
        async with self._conn.execute(
            "SELECT value FROM bot_settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else default

    @_serialized_write
    async def set_bot_setting(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()

    async def get_all_bot_settings(self) -> dict[str, str]:
        async with self._conn.execute("SELECT key, value FROM bot_settings") as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}

    # ══════════════════════════════════════════════════════════════════════════
    # ACCOUNTS (multi-user)
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def add_account(
        self, user_id: int, phone: str, api_id: int, api_hash: str,
        name: Optional[str], session_file: str, proxy: Optional[str] = None,
        max_accounts: Optional[int] = None,
    ) -> Optional[int]:
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            if max_accounts is not None and max_accounts >= 0:
                async with self._conn.execute(
                    "SELECT id FROM accounts WHERE user_id=? AND phone=?",
                    (user_id, phone),
                ) as cur:
                    existing = await cur.fetchone()
                if not existing:
                    async with self._conn.execute(
                        "SELECT COUNT(*) FROM accounts WHERE user_id=?",
                        (user_id,),
                    ) as cur:
                        current_count = (await cur.fetchone())[0]
                    if current_count >= max_accounts:
                        await self._conn.rollback()
                        return None

            async with self._conn.execute(
                """INSERT INTO accounts
                   (user_id, phone, api_id, api_hash, name, session_file, proxy)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, phone) DO UPDATE SET
                   api_id=excluded.api_id, api_hash=excluded.api_hash,
                   name=excluded.name, session_file=excluded.session_file,
                   proxy=excluded.proxy, is_active=1, status='connected'
                   RETURNING id""",
                (user_id, phone, api_id, api_hash, name, session_file, proxy),
            ) as cur:
                row = await cur.fetchone()
            await self._conn.commit()
            return row[0]

    async def get_all_accounts(self, user_id: int) -> list[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE user_id=? ORDER BY created_at", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_account(r) for r in rows]

    async def get_active_accounts(self, user_id: int) -> list[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE user_id=? AND is_active=1 ORDER BY created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_account(r) for r in rows]

    async def get_all_active_accounts(self) -> list[Account]:
        """All active accounts across all users (for manager startup)."""
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE is_active=1 ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_account(r) for r in rows]

    async def get_account(self, account_id: int) -> Optional[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_account(row) if row else None

    async def get_account_by_phone(self, user_id: int, phone: str) -> Optional[Account]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE user_id=? AND phone=?", (user_id, phone)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_account(row) if row else None

    @_serialized_write
    async def update_account_status(
        self, account_id: int, status: str, is_active: int
    ) -> None:
        await self._conn.execute(
            "UPDATE accounts SET status=?, is_active=? WHERE id=?",
            (status, is_active, account_id),
        )
        await self._conn.commit()

    @_serialized_write
    async def delete_account(self, account_id: int) -> None:
        # Remove dependent records first because the schema enables foreign keys.
        # Send history is tied to the account and cannot reference a deleted row.
        await self._conn.execute(
            "DELETE FROM campaign_accounts WHERE account_id=?", (account_id,)
        )
        await self._conn.execute(
            "DELETE FROM send_log WHERE account_id=?", (account_id,)
        )
        await self._conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await self._conn.commit()

    async def count_user_accounts(self, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE user_id=? AND is_active=1", (user_id,)
        ) as cur:
            return (await cur.fetchone())[0]

    # ══════════════════════════════════════════════════════════════════════════
    # CAMPAIGNS
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def create_campaign(self, user_id: int, name: str) -> int:
        async with self._conn.execute(
            "INSERT INTO campaigns (user_id, name) VALUES (?, ?)",
            (user_id, name)
        ) as cur:
            await self._conn.commit()
            return cur.lastrowid

    async def get_campaigns(self, user_id: int) -> list[Campaign]:
        async with self._conn.execute("SELECT * FROM campaigns WHERE user_id=?", (user_id,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_campaign(r) for r in rows]

    async def get_campaign(self, campaign_id: int) -> Optional[Campaign]:
        async with self._conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)) as cur:
            row = await cur.fetchone()
        if row:
            return _row_to_campaign(row)
        return None

    @_serialized_write
    async def delete_campaign(self, campaign_id: int) -> None:
        await self._conn.execute("DELETE FROM send_log WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM target_users WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM campaign_accounts WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
        await self._conn.commit()

    @_serialized_write
    async def update_campaign_text(self, campaign_id: int, text: str) -> None:
        await self._conn.execute("UPDATE campaigns SET text=? WHERE id=?", (text, campaign_id))
        await self._conn.commit()

    @_serialized_write
    async def update_campaign_attachments(
        self, campaign_id: int, image_file_id: Optional[str] = None,
        attach_file_id: Optional[str] = None, attach_file_name: Optional[str] = None
    ) -> None:
        await self._conn.execute(
            """UPDATE campaigns SET image_file_id=?, attach_file_id=?, attach_file_name=?
               WHERE id=?""",
            (image_file_id, attach_file_id, attach_file_name, campaign_id)
        )
        await self._conn.commit()

    @_serialized_write
    async def update_campaign_delays(
        self, campaign_id: int, delay_mode: str, delay_fixed: float,
        delay_min: float, delay_max: float, pause_cycles: float
    ) -> None:
        await self._conn.execute(
            """UPDATE campaigns SET delay_mode=?, delay_fixed=?, delay_min=?, delay_max=?, pause_cycles=?
               WHERE id=?""",
            (delay_mode, delay_fixed, delay_min, delay_max, pause_cycles, campaign_id)
        )
        await self._conn.commit()
        
    @_serialized_write
    async def update_campaign_status(self, campaign_id: int, status: str) -> None:
        await self._conn.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
        await self._conn.commit()

    async def get_campaign_accounts(self, campaign_id: int) -> list[int]:
        async with self._conn.execute(
            """SELECT ca.account_id
               FROM campaign_accounts ca
               JOIN campaigns c ON c.id = ca.campaign_id
               JOIN accounts a ON a.id = ca.account_id AND a.user_id = c.user_id
               WHERE ca.campaign_id=?""",
            (campaign_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    @_serialized_write
    async def assign_account_to_campaign(self, campaign_id: int, account_id: int) -> None:
        try:
            await self._conn.execute(
                """INSERT INTO campaign_accounts (campaign_id, account_id)
                   SELECT ?, ?
                   WHERE EXISTS (
                       SELECT 1 FROM campaigns c
                       JOIN accounts a ON a.user_id = c.user_id
                       WHERE c.id=? AND a.id=?
                   )""",
                (campaign_id, account_id, campaign_id, account_id),
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError:
            await self._conn.rollback()

    @_serialized_write
    async def remove_account_from_campaign(self, campaign_id: int, account_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM campaign_accounts WHERE campaign_id=? AND account_id=?",
            (campaign_id, account_id)
        )
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # TARGET USERS (multi-user)
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def add_targets_bulk(
        self,
        campaign_id: int,
        identifiers: list[str],
        source_file: str,
        max_targets: Optional[int] = None,
    ) -> tuple[int, int]:
        added = 0
        skipped = 0
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            available: Optional[int] = None
            if max_targets is not None and max_targets >= 0:
                async with self._conn.execute(
                    "SELECT COUNT(*) FROM target_users WHERE campaign_id=?",
                    (campaign_id,),
                ) as cur:
                    current_total = (await cur.fetchone())[0]
                available = max(0, max_targets - current_total)

            for ident in identifiers:
                if available is not None and added >= available:
                    break
                try:
                    await self._conn.execute(
                        """INSERT INTO target_users
                           (campaign_id, identifier, source_file)
                           VALUES (?, ?, ?)""",
                        (campaign_id, ident, source_file),
                    )
                    added += 1
                except aiosqlite.IntegrityError:
                    skipped += 1
            await self._conn.commit()
        return added, skipped

    async def get_targets_stats(self, campaign_id: int) -> dict:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM target_users WHERE campaign_id=?", (campaign_id,)
        ) as cur:
            total = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status IN ('sent','blocked','error')",
            (campaign_id,),
        ) as cur:
            processed = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='blocked'", (campaign_id,)
        ) as cur:
            blocked = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='error'", (campaign_id,)
        ) as cur:
            errors = (await cur.fetchone())[0]
        return {
            "total": total,
            "processed": processed,
            "remaining": total - processed,
            "blocked": blocked,
            "errors": errors,
        }

    async def get_unprocessed_identifiers(self, campaign_id: int) -> list[str]:
        async with self._conn.execute(
            """SELECT t.identifier FROM target_users t
               LEFT JOIN send_log s ON t.identifier = s.identifier AND s.campaign_id = t.campaign_id
               WHERE t.campaign_id = ? AND s.identifier IS NULL""",
            (campaign_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    @_serialized_write
    async def clear_send_log(self, campaign_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM send_log WHERE campaign_id=?", (campaign_id,)
        )
        await self._conn.commit()

    @_serialized_write
    async def clear_all_targets(self, campaign_id: int) -> None:
        await self._conn.execute("DELETE FROM send_log WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM target_users WHERE campaign_id=?", (campaign_id,))
        await self._conn.commit()

    @_serialized_write
    async def reset_pending_claims(self, campaign_id: int) -> None:
        """Release claims left by a crashed or forcibly stopped sender."""
        await self._conn.execute(
            "DELETE FROM send_log WHERE campaign_id=? AND status='pending'",
            (campaign_id,),
        )
        await self._conn.commit()

    @_serialized_write
    async def toggle_promo(self, promo_id: int) -> None:
        await self._conn.execute(
            "UPDATE promo_codes SET is_active=NOT is_active WHERE id=?",
            (promo_id,),
        )
        await self._conn.commit()

    @_serialized_write
    async def delete_promo(self, promo_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM promo_activations WHERE promo_id=?",
            (promo_id,),
        )
        await self._conn.execute(
            "DELETE FROM promo_codes WHERE id=?",
            (promo_id,),
        )
        await self._conn.commit()

    @_serialized_write
    async def finalize_payment(
        self, payment_id: int, duration_days: int, referral_reward: float
    ) -> tuple[Subscription, bool, Optional[int]] | None:
        """Atomically mark a payment paid, grant access, and credit referral."""
        now_dt = datetime.utcnow()
        now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires = (now_dt + timedelta(days=duration_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        async with aiosqlite.connect(self._path, timeout=15) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    """SELECT p.*, u.referrer_id
                       FROM payments p
                       JOIN users u ON u.id = p.user_id
                       WHERE p.id = ?""",
                    (payment_id,),
                ) as cur:
                    payment = await cur.fetchone()
                if not payment:
                    await conn.rollback()
                    return None

                payment_key = str(payment_id)
                async with conn.execute(
                    """SELECT * FROM subscriptions
                       WHERE user_id=? AND provider=? AND payment_id=?
                       ORDER BY id DESC LIMIT 1""",
                    (payment["user_id"], payment["provider"], payment_key),
                ) as cur:
                    subscription = await cur.fetchone()

                newly_granted = subscription is None
                referrer_id = payment["referrer_id"]
                if newly_granted:
                    await conn.execute(
                        """UPDATE subscriptions SET is_active=0
                           WHERE user_id=? AND is_active=1""",
                        (payment["user_id"],),
                    )
                    async with conn.execute(
                        """INSERT INTO subscriptions
                           (user_id, plan, expires_at, provider, payment_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            payment["user_id"],
                            payment["plan"],
                            expires,
                            payment["provider"],
                            payment_key,
                        ),
                    ) as cur:
                        subscription_id = cur.lastrowid
                    if referrer_id and referral_reward > 0:
                        await conn.execute(
                            "UPDATE users SET balance=balance+? WHERE id=?",
                            (referral_reward, referrer_id),
                        )
                    async with conn.execute(
                        "SELECT * FROM subscriptions WHERE id=?",
                        (subscription_id,),
                    ) as cur:
                        subscription = await cur.fetchone()

                await conn.execute(
                    """UPDATE payments SET status='paid',
                       paid_at=COALESCE(paid_at, ?) WHERE id=?""",
                    (now, payment_id),
                )
                await conn.commit()
                return (
                    _row_to_sub(subscription),
                    newly_granted,
                    referrer_id if newly_granted else None,
                )
            except Exception:
                await conn.rollback()
                raise

    @_serialized_write
    async def recover_interrupted_mailings(self) -> None:
        """A restarted process has no live senders; clear their stale state."""
        await self._conn.execute(
            "UPDATE campaigns SET status='stopped' WHERE status='running'"
        )
        await self._conn.execute("DELETE FROM send_log WHERE status='pending'")
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # SEND LOG (multi-user)
    # ══════════════════════════════════════════════════════════════════════════

    @_serialized_write
    async def try_claim(self, campaign_id: int, identifier: str, account_id: int) -> bool:
        async with self._write_lock:
            try:
                await self._conn.execute(
                    "INSERT INTO send_log (campaign_id, identifier, account_id, status) VALUES (?,?,?,'pending')",
                    (campaign_id, identifier, account_id),
                )
                await self._conn.commit()
                return True
            except aiosqlite.IntegrityError:
                await self._conn.rollback()
                return False

    @_serialized_write
    async def mark_sent(
        self, campaign_id: int, identifier: str, account_id: int,
        status: str, error_msg: Optional[str] = None
    ) -> None:
        await self._conn.execute(
            """UPDATE send_log SET status=?, error_msg=?, account_id=?,
               sent_at=datetime('now') WHERE campaign_id=? AND identifier=?""",
            (status, error_msg, account_id, campaign_id, identifier),
        )
        await self._conn.commit()

    @_serialized_write
    async def release_claim(self, campaign_id: int, identifier: str) -> None:
        await self._conn.execute(
            "DELETE FROM send_log WHERE campaign_id=? AND identifier=? AND status='pending'",
            (campaign_id, identifier),
        )
        await self._conn.commit()

    async def get_send_stats(self, campaign_id: int) -> dict:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='sent'", (campaign_id,)
        ) as cur:
            sent = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='error'", (campaign_id,)
        ) as cur:
            errors = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='blocked'", (campaign_id,)
        ) as cur:
            blocked = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=? AND status='pending'", (campaign_id,)
        ) as cur:
            pending = (await cur.fetchone())[0]
        async with self._conn.execute(
            "SELECT COUNT(*) FROM target_users WHERE campaign_id=?", (campaign_id,)
        ) as cur:
            total = (await cur.fetchone())[0]
        return {
            "sent": sent, "errors": errors, "blocked": blocked,
            "pending": pending, "total": total,
            "remaining": total - sent - errors - blocked,
        }

    async def get_send_log_page(
        self, campaign_id: int, limit: int, offset: int = 0
    ) -> tuple[list[SendLogEntry], int]:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM send_log WHERE campaign_id=?",
            (campaign_id,),
        ) as cur:
            total = (await cur.fetchone())[0]
        async with self._conn.execute(
            """SELECT id, campaign_id, identifier, account_id, sent_at,
                      status, error_msg
               FROM send_log WHERE campaign_id=?
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (campaign_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        entries = [
            SendLogEntry(
                id=row["id"],
                campaign_id=row["campaign_id"],
                identifier=row["identifier"],
                account_id=row["account_id"],
                sent_at=row["sent_at"],
                status=row["status"],
                error_msg=row["error_msg"],
            )
            for row in rows
        ]
        return entries, total

    # ══════════════════════════════════════════════════════════════════════════
    # SETTINGS (per-user mailing settings)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_setting(self, user_id: int, key: str, default: str = "") -> str:
        async with self._conn.execute(
            "SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else default

    @_serialized_write
    async def set_setting(self, user_id: int, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, value),
        )
        await self._conn.commit()

    async def get_all_settings(self, user_id: int) -> dict[str, str]:
        async with self._conn.execute(
            "SELECT key, value FROM settings WHERE user_id=?", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}

    @_serialized_write
    async def check_and_log_autoresponder(self, user_id: int, tg_id: int) -> bool:
        """Возвращает True если юзера не было в логах (нужно ответить)."""
        try:
            async with self._conn.execute(
                "INSERT INTO autoresponder_log (user_id, tg_id) VALUES (?, ?)",
                (user_id, tg_id)
            ):
                pass
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            await self._conn.rollback()
            return False

    @_serialized_write
    async def remove_autoresponder_log(self, user_id: int, tg_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM autoresponder_log WHERE user_id=? AND tg_id=?",
            (user_id, tg_id),
        )
        await self._conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# ROW CONVERTERS
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_user(row: aiosqlite.Row) -> BotUser:
    return BotUser(
        id=row["id"], tg_id=row["tg_id"],
        username=row["username"], full_name=row["full_name"],
        is_banned=row["is_banned"], agreed_at=row["agreed_at"],
        referrer_id=row["referrer_id"] if "referrer_id" in row.keys() else None,
        balance=row["balance"] if "balance" in row.keys() else 0.0,
        created_at=row["created_at"],
    )


def _row_to_sub(row: aiosqlite.Row) -> Subscription:
    return Subscription(
        id=row["id"], user_id=row["user_id"], plan=row["plan"],
        expires_at=row["expires_at"], payment_id=row["payment_id"],
        provider=row["provider"], is_active=row["is_active"],
        created_at=row["created_at"],
    )


def _row_to_promo(row: aiosqlite.Row) -> PromoCode:
    return PromoCode(
        id=row["id"], code=row["code"], plan=row["plan"],
        duration_days=row["duration_days"], max_uses=row["max_uses"],
        used_count=row["used_count"], created_by=row["created_by"],
        expires_at=row["expires_at"], is_active=row["is_active"],
        created_at=row["created_at"],
    )


def _row_to_payment(row: aiosqlite.Row) -> Payment:
    return Payment(
        id=row["id"], user_id=row["user_id"], provider=row["provider"],
        plan=row["plan"], amount=row["amount"], currency=row["currency"],
        external_id=row["external_id"], status=row["status"],
        created_at=row["created_at"], paid_at=row["paid_at"],
    )


def _row_to_account(row: aiosqlite.Row) -> Account:
    keys = row.keys()
    return Account(
        id=row["id"],
        user_id=row["user_id"] if "user_id" in keys else 0,
        phone=row["phone"], api_id=row["api_id"], api_hash=row["api_hash"],
        name=row["name"], session_file=row["session_file"],
        proxy=row["proxy"] if "proxy" in keys else None,
        is_active=row["is_active"],
        status=row["status"] if "status" in keys else "connected",
        created_at=row["created_at"],
    )

def _row_to_campaign(row: aiosqlite.Row) -> Campaign:
    return Campaign(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        text=row["text"], 
        image_file_id=row["image_file_id"],
        attach_file_id=row["attach_file_id"],
        attach_file_name=row["attach_file_name"],
        delay_mode=row["delay_mode"], delay_fixed=row["delay_fixed"],
        delay_min=row["delay_min"], delay_max=row["delay_max"],
        pause_cycles=row["pause_cycles"], status=row["status"],
        created_at=row["created_at"],
    )
