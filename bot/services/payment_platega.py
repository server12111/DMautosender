"""
Platega payment integration.
API Base: https://app.platega.io
Auth: X-MerchantId + X-Secret headers
"""
import logging
from typing import Optional
import aiohttp

from ..config import config

logger = logging.getLogger("dmsender.platega")

BASE_URL = "https://app.platega.io"


def _headers() -> dict:
    return {
        "X-MerchantId": config.PLATEGA_MERCHANT_ID,
        "X-Secret": config.PLATEGA_SECRET,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def create_payment_link(
    order_id: str,
    amount: float,
    currency: str = "USD",
    description: str = "DMautosender Subscription",
    success_url: str = "",
) -> Optional[dict]:
    """
    Creates a Platega payment link.
    Returns dict with 'payment_url' and 'payment_id', or None on error.
    """
    if not config.PLATEGA_MERCHANT_ID or not config.PLATEGA_SECRET:
        logger.warning("Platega credentials not configured")
        return None

    payload = {
        "paymentDetails": {
            "amount": amount,
            "currency": currency,
        },
        "description": description,
        "payload": order_id,
    }
    if success_url:
        payload["return"] = success_url

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/v2/transaction/process",
                json=payload,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                logger.debug("Platega create_payment response: %s", data)
                payment_url = (
                    data.get("url")
                    or data.get("redirect")
                    or data.get("payment_url")
                )
                payment_id = (
                    data.get("transactionId")
                    or data.get("payment_id")
                    or data.get("id")
                )
                if resp.status in (200, 201) and payment_url and payment_id:
                    return {
                        "payment_url": payment_url,
                        "payment_id": payment_id,
                    }
                logger.error("Platega error: %s %s", resp.status, data)
                return None
    except Exception as e:
        logger.error("Platega create_payment exception: %s", e)
        return None


async def check_payment_status(payment_id: str) -> Optional[str]:
    """
    Returns status string: 'paid' | 'pending' | 'failed' | None on error.
    """
    if not config.PLATEGA_MERCHANT_ID:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/transaction/{payment_id}",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                status = str(data.get("status", "")).upper()
                if status == "CONFIRMED":
                    return "paid"
                if status == "PENDING":
                    return "pending"
                if status in {
                    "CANCELED",
                    "CANCELLED",
                    "EXPIRED",
                    "FAILED",
                    "CHARGEBACKED",
                }:
                    return "failed"
                return None
    except Exception as e:
        logger.error("Platega check_status exception: %s", e)
        return None


async def poll_until_paid(
    payment_id: str,
    timeout_seconds: int = 900,
    interval_seconds: int = 10,
) -> bool:
    """
    Polls Platega every `interval_seconds` for up to `timeout_seconds`.
    Returns True if payment confirmed as 'paid'.
    """
    import asyncio
    elapsed = 0
    while elapsed < timeout_seconds:
        status = await check_payment_status(payment_id)
        if status == "paid":
            return True
        if status == "failed":
            return False
        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds
    return False
