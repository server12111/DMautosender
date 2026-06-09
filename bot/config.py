from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


class Config:
    # ── Bot ───────────────────────────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: list[int] = [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "").split(",")
        if x.strip().isdigit()
    ]

    # ── Paths ─────────────────────────────────────────────────────────────────
    DATABASE_PATH: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/bot.db")
    SESSIONS_PATH: Path = BASE_DIR / os.getenv("SESSIONS_PATH", "data/sessions")
    LOGS_PATH: Path = BASE_DIR / os.getenv("LOGS_PATH", "data/logs")

    # ── Telegram Userbot defaults ─────────────────────────────────────────────
    DEFAULT_API_ID: int = int(os.getenv("DEFAULT_API_ID", "2040"))
    DEFAULT_API_HASH: str = os.getenv("DEFAULT_API_HASH", "b18441a1ff607e10a989891a5462e627")

    # ── Platega Payment ───────────────────────────────────────────────────────
    PLATEGA_MERCHANT_ID: str = os.getenv("PLATEGA_MERCHANT_ID", "")
    PLATEGA_SECRET: str = os.getenv("PLATEGA_SECRET", "")
    PLATEGA_BASE_URL: str = "https://app.platega.io"

    # ── CryptoBot Payment ─────────────────────────────────────────────────────
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    CRYPTOBOT_BASE_URL: str = "https://pay.crypt.bot/api"

    # ── TonCenter (TON) ───────────────────────────────────────────────────────
    TON_WALLET: str = os.getenv("TON_WALLET", "")
    TONCENTER_API_KEY: str = os.getenv("TONCENTER_API_KEY", "")

    # ── Subscription defaults (can be overridden in admin panel via DB) ───────
    DEFAULT_TRIAL_DAYS: int = int(os.getenv("DEFAULT_TRIAL_DAYS", "3"))
    DEFAULT_PRO_PRICE_USD: float = float(os.getenv("DEFAULT_PRO_PRICE_USD", "3.0"))
    DEFAULT_BUSINESS_PRICE_USD: float = float(os.getenv("DEFAULT_BUSINESS_PRICE_USD", "5.0"))

    # ── Legal URLs ────────────────────────────────────────────────────────────
    PRIVACY_URL: str = os.getenv("PRIVACY_URL", "https://telegra.ph/")
    TERMS_URL: str = os.getenv("TERMS_URL", "https://telegra.ph/")

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.SESSIONS_PATH.mkdir(parents=True, exist_ok=True)
        cls.LOGS_PATH.mkdir(parents=True, exist_ok=True)


config = Config()
