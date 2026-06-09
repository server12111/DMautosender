"""
Subscription service — checks plans, limits, activates promos.
"""
from datetime import datetime
from typing import Optional

from ..database.db import Database
from ..database.models import Subscription, PromoCode, PLAN_LIMITS, get_plan_limit


class SubscriptionService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_plan(self, user_id: int) -> str:
        return await self._db.get_subscription_plan(user_id)

    async def get_subscription(self, user_id: int) -> Optional[Subscription]:
        return await self._db.get_active_subscription(user_id)

    async def get_limit(self, user_id: int, key: str) -> int:
        plan = await self.get_plan(user_id)
        return get_plan_limit(plan, key)

    async def can_add_account(self, user_id: int, current_count: int) -> bool:
        limit = await self.get_limit(user_id, "max_accounts")
        if limit == -1:
            return True  # unlimited
        return current_count < limit

    async def can_add_targets(self, user_id: int, current_count: int) -> bool:
        limit = await self.get_limit(user_id, "max_targets")
        if limit == -1:
            return True
        return current_count < limit

    async def activate_promo(self, user_id: int, code: str) -> tuple[bool, str]:
        """
        Returns (success, message).
        """
        promo = await self._db.get_promo_by_code(code)
        if not promo:
            return False, "❌ Промокод не найден или уже недействителен."

        if not promo.is_active:
            return False, "❌ Промокод деактивирован."

        if promo.expires_at:
            try:
                exp = datetime.strptime(promo.expires_at, "%Y-%m-%d %H:%M:%S")
                if exp < datetime.utcnow():
                    return False, "❌ Срок действия промокода истёк."
            except Exception:
                pass

        if promo.used_count >= promo.max_uses:
            return False, "❌ Промокод исчерпан (все активации использованы)."

        ok = await self._db.use_promo(promo.id, user_id)
        if not ok:
            return False, "❌ Вы уже активировали этот промокод."

        sub = await self._db.create_subscription(
            user_id=user_id,
            plan=promo.plan,
            duration_days=promo.duration_days,
            provider="promo",
            payment_id=f"promo:{promo.code}",
        )

        plan_info = PLAN_LIMITS.get(promo.plan, {})
        label = plan_info.get("label", promo.plan.upper())
        return True, (
            f"✅ Промокод активирован!\n\n"
            f"📋 План: <b>{label}</b>\n"
            f"⏰ Действует до: <b>{sub.expires_at[:10]}</b>"
        )

    async def activate_payment(
        self,
        user_id: int,
        plan: str,
        duration_days: int,
        provider: str,
        payment_id: str,
    ) -> Subscription:
        """Called after successful payment."""
        await self._db.update_payment_status(
            int(payment_id.split(":")[1]) if ":" in payment_id else 0,
            "paid",
        )
        return await self._db.create_subscription(
            user_id=user_id,
            plan=plan,
            duration_days=duration_days,
            provider=provider,
            payment_id=payment_id,
        )

    async def get_plan_info_text(self, user_id: int) -> str:
        sub = await self.get_subscription(user_id)
        plan = sub.plan if sub else "free"
        info = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        max_acc = info["max_accounts"]
        max_tgt = info["max_targets"]
        acc_str = "∞" if max_acc == -1 else str(max_acc)
        tgt_str = "∞" if max_tgt == -1 else f"{max_tgt:,}"

        expires_str = ""
        if sub and sub.expires_at:
            expires_str = f"\n⏰ Действует до: <b>{sub.expires_at[:10]}</b>"

        return (
            f"💳 Ваш план: <b>{info['emoji']} {info['label']}</b>{expires_str}\n"
            f"👤 Аккаунтов: <b>{acc_str}</b>\n"
            f"📋 Получателей: <b>{tgt_str}</b>"
        )
