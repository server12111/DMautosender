"""
TON / TonCenter payment integration.
Checks incoming TON transactions with a unique comment/payload.

Flow:
1. Generate unique payment payload: f"dm_{user_id}_{payment_db_id}"
2. User sends TON to the configured wallet address with this comment
3. We poll TonCenter API to detect the incoming transaction
"""
import logging
import hashlib
from typing import Optional
import aiohttp

from ..config import config

logger = logging.getLogger("dmsender.toncenter")

TONCENTER_API = "https://toncenter.com/api/v2"


def generate_payload(user_id: int, payment_id: int) -> str:
    """Unique payload/comment for the TON transaction."""
    return f"dm{user_id}p{payment_id}"


def get_payment_info(user_id: int, payment_id: int, amount_ton: float) -> dict:
    """Returns all info needed to display a TON payment request."""
    payload = generate_payload(user_id, payment_id)
    return {
        "wallet": config.TON_WALLET,
        "amount_ton": amount_ton,
        "payload": payload,
        "deeplink": f"ton://transfer/{config.TON_WALLET}?amount={int(amount_ton * 1e9)}&text={payload}",
        "tonkeeper_url": f"https://app.tonkeeper.com/transfer/{config.TON_WALLET}?amount={int(amount_ton * 1e9)}&text={payload}",
    }


async def check_transaction_received(
    user_id: int, payment_id: int, amount_ton: float
) -> bool:
    """
    Checks TonCenter for an incoming transaction matching our payload.
    Returns True if found and amount matches.
    """
    if not config.TON_WALLET:
        return False

    payload = generate_payload(user_id, payment_id)
    nano_amount = int(amount_ton * 1e9)

    try:
        params = {
            "address": config.TON_WALLET,
            "limit": 20,
            "to_lt": 0,
            "archival": "false",
        }
        if config.TONCENTER_API_KEY:
            params["api_key"] = config.TONCENTER_API_KEY

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TONCENTER_API}/getTransactions",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if not data.get("ok"):
            return False

        for tx in data.get("result", []):
            msg = tx.get("in_msg", {})
            comment = msg.get("message", "")
            value = int(msg.get("value", 0))
            if comment == payload and value >= nano_amount * 0.99:  # 1% tolerance
                return True

        return False

    except Exception as e:
        logger.error("TonCenter check_transaction exception: %s", e)
        return False


async def poll_until_paid(
    user_id: int,
    payment_id: int,
    amount_ton: float,
    timeout_seconds: int = 900,
    interval_seconds: int = 20,
) -> bool:
    """
    Polls TonCenter for the incoming transaction.
    Returns True if payment detected.
    """
    import asyncio
    elapsed = 0
    while elapsed < timeout_seconds:
        received = await check_transaction_received(user_id, payment_id, amount_ton)
        if received:
            return True
        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds
    return False
