"""
CryptoBot (@CryptoBot) payment integration.
API: https://pay.crypt.bot/api
Auth: Crypto-Pay-API-Token header
Docs: https://help.crypt.bot/crypto-pay-api
"""
import logging
from typing import Optional
import aiohttp

from ..config import config

logger = logging.getLogger("dmsender.cryptobot")

BASE_URL = "https://pay.crypt.bot/api"


def _headers() -> dict:
    return {
        "Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN,
        "Content-Type": "application/json",
    }


async def create_invoice(
    amount: float,
    asset: str = "USDT",
    description: str = "DMautosender Subscription",
    payload: str = "",
    expires_in: int = 3600,
) -> Optional[dict]:
    """
    Creates CryptoBot invoice.
    Returns dict with 'invoice_id', 'pay_url', or None on error.
    """
    if not config.CRYPTOBOT_TOKEN:
        logger.warning("CryptoBot token not configured")
        return None

    body = {
        "asset": asset,
        "amount": str(round(amount, 2)),
        "description": description,
        "payload": payload,
        "expires_in": expires_in,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/createInvoice",
                json=body,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                logger.debug("CryptoBot createInvoice: %s", data)
                if data.get("ok") and data.get("result"):
                    result = data["result"]
                    pay_url = (
                        result.get("bot_invoice_url")
                        or result.get("mini_app_invoice_url")
                        or result.get("web_app_invoice_url")
                        or result.get("pay_url")
                    )
                    if not pay_url:
                        logger.error("CryptoBot response has no invoice URL: %s", result)
                        return None
                    return {
                        "invoice_id": result["invoice_id"],
                        "pay_url": pay_url,
                        "bot_invoice_url": result.get("bot_invoice_url", pay_url),
                    }
                logger.error("CryptoBot error: %s", data)
                return None
    except Exception as e:
        logger.error("CryptoBot createInvoice exception: %s", e)
        return None


async def check_invoice_status(invoice_id: int) -> Optional[str]:
    """
    Returns invoice status: 'active' | 'paid' | 'expired' | None on error.
    """
    if not config.CRYPTOBOT_TOKEN:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/getInvoices",
                json={"invoice_ids": str(invoice_id)},
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                result = data.get("result")
                if isinstance(result, dict):
                    invoices = result.get("items", [])
                elif isinstance(result, list):
                    invoices = result
                else:
                    invoices = []
                if data.get("ok") and invoices:
                    return invoices[0].get("status")
                return None
    except Exception as e:
        logger.error("CryptoBot getInvoices exception: %s", e)
        return None


async def poll_until_paid(
    invoice_id: int,
    timeout_seconds: int = 900,
    interval_seconds: int = 10,
) -> bool:
    """
    Polls CryptoBot every interval_seconds for up to timeout_seconds.
    Returns True if paid.
    """
    import asyncio
    elapsed = 0
    while elapsed < timeout_seconds:
        status = await check_invoice_status(invoice_id)
        if status == "paid":
            return True
        if status == "expired":
            return False
        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds
    return False
