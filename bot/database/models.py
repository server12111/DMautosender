from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- ── Users ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER NOT NULL UNIQUE,
    username    TEXT,
    full_name   TEXT,
    is_banned   INTEGER DEFAULT 0,
    agreed_at   TEXT,
    referrer_id INTEGER,
    balance     REAL DEFAULT 0.0,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ── Subscriptions ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free',
    expires_at  TEXT,
    payment_id  TEXT,
    provider    TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ── Promo Codes ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promo_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    plan            TEXT NOT NULL DEFAULT 'pro',
    duration_days   INTEGER NOT NULL DEFAULT 30,
    max_uses        INTEGER DEFAULT 1,
    used_count      INTEGER DEFAULT 0,
    created_by      INTEGER,
    expires_at      TEXT,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ── Promo Activations ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promo_activations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    activated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(promo_id, user_id),
    FOREIGN KEY (promo_id) REFERENCES promo_codes(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ── Payments ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    provider        TEXT NOT NULL,
    plan            TEXT NOT NULL,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    external_id     TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    paid_at         TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ── Accounts (multi-user) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 0,
    phone       TEXT NOT NULL,
    name        TEXT,
    api_id      INTEGER NOT NULL,
    api_hash    TEXT NOT NULL,
    session_file TEXT,
    proxy       TEXT,
    is_active   INTEGER DEFAULT 1,
    status      TEXT DEFAULT 'connected',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, phone)
);

-- ── Campaigns ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    text            TEXT DEFAULT '',
    image_file_id   TEXT,
    attach_file_id  TEXT,
    attach_file_name TEXT,
    delay_mode      TEXT DEFAULT 'fixed',
    delay_fixed     REAL DEFAULT 10.0,
    delay_min       REAL DEFAULT 5.0,
    delay_max       REAL DEFAULT 30.0,
    pause_cycles    REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'stopped',
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ── Campaign Accounts (Many-to-Many) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_accounts (
    campaign_id INTEGER NOT NULL,
    account_id  INTEGER NOT NULL,
    UNIQUE(campaign_id, account_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

-- ── Target users (per-campaign) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS target_users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    identifier  TEXT NOT NULL,
    source_file TEXT,
    loaded_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(campaign_id, identifier),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

-- ── Send log (per-campaign) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS send_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    identifier  TEXT NOT NULL,
    account_id  INTEGER NOT NULL,
    sent_at     TEXT DEFAULT (datetime('now')),
    status      TEXT DEFAULT 'sent',
    error_msg   TEXT,
    UNIQUE(campaign_id, identifier),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

-- ── Settings (multi-user) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    UNIQUE(user_id, key)
);

-- ── Bot global settings ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- ── Autoresponder log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS autoresponder_log (
    user_id     INTEGER NOT NULL,
    tg_id       INTEGER NOT NULL,
    replied_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, tg_id)
);
"""

MIGRATIONS = [
    # Add user_id to accounts if upgrading from old schema
    "ALTER TABLE accounts ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
    # Add user_id to target_users
    "ALTER TABLE target_users ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
    # Add user_id to send_log
    "ALTER TABLE send_log ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
    # Add user_id to settings (rename old key-only unique constraint handled by recreate)
    "ALTER TABLE settings ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
    # accounts status column (legacy)
    "ALTER TABLE accounts ADD COLUMN status TEXT DEFAULT 'connected'",
    # proxy column for accounts
    "ALTER TABLE accounts ADD COLUMN proxy TEXT",
]

# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BotUser:
    id: int
    tg_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    is_banned: int = 0
    agreed_at: Optional[str] = None
    referrer_id: Optional[int] = None
    balance: float = 0.0
    created_at: Optional[str] = None


@dataclass
class Subscription:
    id: int
    user_id: int
    plan: str = "free"
    expires_at: Optional[str] = None
    payment_id: Optional[str] = None
    provider: Optional[str] = None
    is_active: int = 1
    created_at: Optional[str] = None


@dataclass
class PromoCode:
    id: int
    code: str
    plan: str = "pro"
    duration_days: int = 30
    max_uses: int = 1
    used_count: int = 0
    created_by: Optional[int] = None
    expires_at: Optional[str] = None
    is_active: int = 1
    created_at: Optional[str] = None


@dataclass
class Payment:
    id: int
    user_id: int
    provider: str
    plan: str
    amount: float
    currency: str = "USD"
    external_id: Optional[str] = None
    status: str = "pending"
    created_at: Optional[str] = None
    paid_at: Optional[str] = None


@dataclass
class Account:
    id: int
    user_id: int
    phone: str
    api_id: int
    api_hash: str
    name: Optional[str] = None
    session_file: Optional[str] = None
    proxy: Optional[str] = None
    is_active: int = 1
    status: str = "connected"
    created_at: Optional[str] = None

@dataclass
class Campaign:
    id: int
    user_id: int
    name: str
    text: str = ""
    image_file_id: Optional[str] = None
    attach_file_id: Optional[str] = None
    attach_file_name: Optional[str] = None
    delay_mode: str = "fixed"
    delay_fixed: float = 10.0
    delay_min: float = 5.0
    delay_max: float = 30.0
    pause_cycles: float = 0.0
    status: str = "stopped"
    created_at: Optional[str] = None



@dataclass
class TargetUser:
    id: int
    campaign_id: int
    identifier: str
    source_file: Optional[str] = None
    loaded_at: Optional[str] = None


@dataclass
class SendLogEntry:
    id: int
    campaign_id: int
    identifier: str
    account_id: int
    sent_at: Optional[str] = None
    status: str = "sent"
    error_msg: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# PLAN LIMITS
# ─────────────────────────────────────────────────────────────────────────────

PLAN_LIMITS = {
    "free": {
        "max_accounts": 1,
        "max_targets": 100,
        "label": "Free",
        "emoji": "🆓",
    },
    "pro": {
        "max_accounts": 5,
        "max_targets": 10_000,
        "label": "Pro",
        "emoji": "⭐",
    },
    "business": {
        "max_accounts": -1,   # unlimited
        "max_targets": -1,    # unlimited
        "label": "Business",
        "emoji": "💎",
    },
}


def get_plan_limit(plan: str, key: str, default=0):
    """Returns plan limit; -1 means unlimited."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).get(key, default)
