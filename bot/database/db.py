import aiosqlite
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta

from .models import (
    SCHEMA, MIGRATIONS,
    Account, TargetUser, SendLogEntry,
    BotUser, Subscription, PromoCode, Payment, Campaign,
)


class Database:
    def __init__(self, db_path: Path) -> None:
        self._path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        for sql in MIGRATIONS:
            try:
                await self._conn.execute(sql)
                await self._conn.commit()
            except Exception:
                pass

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ══════════════════════════════════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════════════════════════════════

    async def get_or_create_user(
        self, tg_id: int, username: Optional[str], full_name: Optional[str], referrer_id: Optional[int] = None
    ) -> BotUser:
        async with self._conn.execute(
            "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            # Update username/name if changed
            await self._conn.execute(
                "UPDATE users SET username=?, full_name=? WHERE tg_id=?",
                (username, full_name, tg_id),
            )
            await self._conn.commit()
            return _row_to_user(row)

        async with self._conn.execute(
            "INSERT INTO users (tg_id, username, full_name, referrer_id) VALUES (?, ?, ?, ?)",
            (tg_id, username, full_name, referrer_id),
        ) as cur:
            user_id = cur.lastrowid
        await self._conn.commit()

        # Give free trial subscription
        expires = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        await self._conn.execute(
            "INSERT INTO subscriptions (user_id, plan, expires_at, provider) VALUES (?, 'free', ?, 'trial')",
            (user_id, expires),
        )
        await self._conn.commit()

        async with self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
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

    async def set_user_agreed(self, tg_id: int) -> None:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await self._conn.execute(
            "UPDATE users SET agreed_at = ? WHERE tg_id = ?", (now, tg_id)
        )
        await self._conn.commit()

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

    async def use_promo(self, promo_id: int, user_id: int) -> bool:
        """Returns False if already used by this user or exhausted."""
        async with self._conn.execute(
            "SELECT * FROM promo_activations WHERE promo_id=? AND user_id=?",
            (promo_id, user_id),
        ) as cur:
            if await cur.fetchone():
                return False  # Already used

        try:
            await self._conn.execute(
                "INSERT INTO promo_activations (promo_id, user_id) VALUES (?, ?)",
                (promo_id, user_id),
            )
            await self._conn.execute(
                "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?",
                (promo_id,),
            )
            # Deactivate if max_uses reached
            await self._conn.execute(
                "UPDATE promo_codes SET is_active = 0 WHERE id = ? AND used_count >= max_uses",
                (promo_id,),
            )
            await self._conn.commit()
            return True
        except Exception:
            return False

    async def deactivate_promo(self, promo_id: int) -> None:
        await self._conn.execute(
            "UPDATE promo_codes SET is_active = 0 WHERE id = ?", (promo_id,)
        )
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # PAYMENTS
    # ══════════════════════════════════════════════════════════════════════════

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

    async def add_account(
        self, user_id: int, phone: str, api_id: int, api_hash: str,
        name: Optional[str], session_file: str, proxy: Optional[str] = None
    ) -> int:
        async with self._conn.execute(
            """INSERT INTO accounts (user_id, phone, api_id, api_hash, name, session_file, proxy)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, phone) DO UPDATE SET
               api_id=excluded.api_id, api_hash=excluded.api_hash,
               name=excluded.name, session_file=excluded.session_file, proxy=excluded.proxy,
               is_active=1, status='connected'""",
            (user_id, phone, api_id, api_hash, name, session_file, proxy),
        ) as cur:
            await self._conn.commit()
            if cur.lastrowid:
                return cur.lastrowid
        async with self._conn.execute(
            "SELECT id FROM accounts WHERE user_id=? AND phone=?", (user_id, phone)
        ) as cur:
            row = await cur.fetchone()
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

    async def update_account_status(
        self, account_id: int, status: str, is_active: int
    ) -> None:
        await self._conn.execute(
            "UPDATE accounts SET status=?, is_active=? WHERE id=?",
            (status, is_active, account_id),
        )
        await self._conn.commit()

    async def delete_account(self, account_id: int) -> None:
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

    async def delete_campaign(self, campaign_id: int) -> None:
        await self._conn.execute("DELETE FROM send_log WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM target_users WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM campaign_accounts WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
        await self._conn.commit()

    async def update_campaign_text(self, campaign_id: int, text: str) -> None:
        await self._conn.execute("UPDATE campaigns SET text=? WHERE id=?", (text, campaign_id))
        await self._conn.commit()

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
        
    async def update_campaign_status(self, campaign_id: int, status: str) -> None:
        await self._conn.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
        await self._conn.commit()

    async def get_campaign_accounts(self, campaign_id: int) -> list[int]:
        async with self._conn.execute(
            "SELECT account_id FROM campaign_accounts WHERE campaign_id=?", (campaign_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def assign_account_to_campaign(self, campaign_id: int, account_id: int) -> None:
        try:
            await self._conn.execute(
                "INSERT INTO campaign_accounts (campaign_id, account_id) VALUES (?, ?)",
                (campaign_id, account_id)
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError:
            pass

    async def remove_account_from_campaign(self, campaign_id: int, account_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM campaign_accounts WHERE campaign_id=? AND account_id=?",
            (campaign_id, account_id)
        )
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # TARGET USERS (multi-user)
    # ══════════════════════════════════════════════════════════════════════════

    async def add_targets_bulk(
        self, campaign_id: int, identifiers: list[str], source_file: str
    ) -> tuple[int, int]:
        added = 0
        skipped = 0
        for ident in identifiers:
            try:
                await self._conn.execute(
                    "INSERT INTO target_users (campaign_id, identifier, source_file) VALUES (?, ?, ?)",
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

    async def clear_send_log(self, campaign_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM send_log WHERE campaign_id=?", (campaign_id,)
        )
        await self._conn.commit()

    async def clear_all_targets(self, campaign_id: int) -> None:
        await self._conn.execute("DELETE FROM send_log WHERE campaign_id=?", (campaign_id,))
        await self._conn.execute("DELETE FROM target_users WHERE campaign_id=?", (campaign_id,))
        await self._conn.commit()

    # ══════════════════════════════════════════════════════════════════════════
    # SEND LOG (multi-user)
    # ══════════════════════════════════════════════════════════════════════════

    async def try_claim(self, campaign_id: int, identifier: str, account_id: int) -> bool:
        try:
            await self._conn.execute(
                "INSERT INTO send_log (campaign_id, identifier, account_id, status) VALUES (?,?,?,'pending')",
                (campaign_id, identifier, account_id),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

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

    # ══════════════════════════════════════════════════════════════════════════
    # SETTINGS (per-user mailing settings)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_setting(self, user_id: int, key: str, default: str = "") -> str:
        async with self._conn.execute(
            "SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else default

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

    async def add_target_users_bulk(self, user_id: int, identifiers: list[str]) -> int:
        added = 0
        for ident in identifiers:
            try:
                async with self._conn.execute(
                    "INSERT INTO target_users (user_id, identifier) VALUES (?, ?)",
                    (user_id, ident),
                ) as cur:
                    if cur.rowcount > 0:
                        added += 1
            except aiosqlite.IntegrityError:
                pass
        await self._conn.commit()
        return added

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
            return False


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
