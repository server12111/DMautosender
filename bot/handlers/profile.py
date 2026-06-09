"""
User profile handler.
Shows subscription status, promo activation, support link, legal docs.
"""
import logging
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from ..keyboards.inline import profile_kb, cancel_to_profile_kb
from ..database.db import Database
from ..database.models import BotUser, PLAN_LIMITS
from ..services.subscription import SubscriptionService
from ..utils.emoji import e

logger = logging.getLogger("dmsender.profile")
router = Router()


class PromoStates(StatesGroup):
    waiting_code = State()


async def _profile_text(user: BotUser, db: Database) -> str:
    svc = SubscriptionService(db)
    sub = await svc.get_subscription(user.id)
    plan = sub.plan if sub else "free"
    info = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    max_acc = info["max_accounts"]
    max_tgt = info["max_targets"]
    acc_str = "∞" if max_acc == -1 else str(max_acc)
    tgt_str = "∞" if max_tgt == -1 else f"{max_tgt:,}"

    expires_line = ""
    if sub and sub.expires_at:
        expires_line = f"\n{e('⏰')} До: <b>{sub.expires_at[:10]}</b>"

    uname = f"@{user.username}" if user.username else "—"
    reg_date = user.created_at[:10] if user.created_at else "—"

    from ..config import config
    bot_username = await db.get_bot_setting("bot_username", "DMautosenderBot")
    referrals_count = await db.get_referrals_count(user.id)
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    
    return (
        f"{e('👤')} <b>ВАШ ПРОФИЛЬ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('🆔')} <b>Ваш ID:</b> <code>{user.tg_id}</code>\n"
        f"{e('💎')} <b>Подписка:</b> {info['emoji']} {info['label']}{expires_line}\n\n"
        f"{e('📊')} <b>ДОСТУПНЫЕ ЛИМИТЫ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('📱')} Аккаунтов: <b>{acc_str}</b>\n"
        f"{e('🎯')} Получателей: <b>{tgt_str}</b>\n\n"
        f"{e('🤝')} <b>ПАРТНЕРСКАЯ ПРОГРАММА</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>{e('🎁')} Приглашайте друзей и получайте <b>10%</b> от их покупок прямо на баланс!</i>\n\n"
        f"{e('🔗')} Ваша ссылка: <code>{ref_link}</code>\n"
        f"{e('👥')} Приглашено: <b>{referrals_count}</b> чел.\n"
        f"{e('💳')} <b>Ваш баланс:</b> <b>${user.balance:.2f}</b>"
    )

@router.callback_query(F.data == "profile:show", StateFilter("*"))
async def cb_profile(callback: CallbackQuery, db: Database) -> None:
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: пользователь не найден.", show_alert=True)
        return

    support_username = await db.get_bot_setting("support_username", "")
    text = await _profile_text(user, db)
    try:
        await callback.message.edit_text(
            text, reply_markup=profile_kb(support_username), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=profile_kb(support_username), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "promo:activate", StateFilter("*"))
async def cb_promo_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoStates.waiting_code)
    await callback.message.edit_text(
        f"{e('🎟')} <b>Активация промокода</b>\n\n"
        f"Введите ваш промокод:\n"
        f"<i>Промокоды регистронезависимы (PROMO123 = promo123)</i>",
        reply_markup=cancel_to_profile_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(PromoStates.waiting_code))
async def fsm_promo_code(message: Message, state: FSMContext, db: Database) -> None:
    code = (message.text or "").strip()
    if not code:
        await message.answer("❌ Введите промокод:", reply_markup=cancel_to_profile_kb())
        return

    user = await db.get_user_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("❌ Ошибка пользователя.", reply_markup=cancel_to_profile_kb())
        return

    svc = SubscriptionService(db)
    ok, msg = await svc.activate_promo(user.id, code)
    await state.clear()

    support_username = await db.get_bot_setting("support_username", "")
    text = await _profile_text(user, db)

    await message.answer(
        f"{msg}\n\n{text}",
        reply_markup=profile_kb(support_username),
        parse_mode="HTML",
    )
