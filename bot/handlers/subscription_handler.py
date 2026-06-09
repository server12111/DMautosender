"""
Subscription & payment handler.
Shows plans, handles Platega / CryptoBot / TON payment flows.
"""
import asyncio
import logging
from aiogram.filters import StateFilter
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from ..keyboards.inline import (
    plans_kb, payment_provider_kb, payment_waiting_kb,
    ton_payment_kb, payment_success_kb, profile_kb,
)
from ..database.db import Database
from ..database.models import PLAN_LIMITS
from ..services.subscription import SubscriptionService
from ..services import payment_platega, payment_cryptobot, payment_toncenter
from ..utils.emoji import e
from ..config import config

logger = logging.getLogger("dmsender.subscription")
router = Router()

PLAN_DURATION_DAYS = 30  # All plans are monthly


async def _plans_text(db: Database) -> str:
    pro_price = await db.get_bot_setting("pro_price", str(config.DEFAULT_PRO_PRICE_USD))
    biz_price = await db.get_bot_setting("business_price", str(config.DEFAULT_BUSINESS_PRICE_USD))
    return (
        f"{e('👑')} <b>ТАРИФНЫЕ ПЛАНЫ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{e('🆓')} <b>Пакет «Free»</b> — Бесплатно\n"
        f"<i>Для старта и ознакомления</i>\n"
        f" ├ {e('📱')} 1 аккаунт Telegram\n"
        f" └ {e('🎯')} До 100 получателей/мес\n\n"
        f"{e('⭐')} <b>Пакет «Pro»</b> — <b>${pro_price}/мес</b>\n"
        f"<i>Выбор уверенных пользователей</i>\n"
        f" ├ {e('📱')} До 5 аккаунтов\n"
        f" └ {e('🎯')} До 10,000 получателей/мес\n\n"
        f"{e('💎')} <b>Пакет «Business»</b> — <b>${biz_price}/мес</b>\n"
        f"<i>Максимальная производительность</i>\n"
        f" ├ {e('📱')} <b>Безлимит</b> аккаунтов\n"
        f" └ {e('🎯')} <b>Безлимит</b> получателей\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('🎟')} <i>Есть промокод? Активируйте его в разделе «Профиль».</i>"
    )


@router.callback_query(F.data == "support:contact", StateFilter("*"))
async def cb_support_contact(callback: CallbackQuery, db: Database) -> None:
    username = await db.get_bot_setting("support_username", "")
    if username:
        await callback.answer(f"Поддержка: @{username.lstrip('@')}", show_alert=True)
    else:
        await callback.answer("Поддержка не настроена. Обратитесь к администратору.", show_alert=True)


@router.callback_query(F.data == "sub:plans", StateFilter("*"))
async def cb_plans(callback: CallbackQuery, db: Database) -> None:
    pro_price = await db.get_bot_setting("pro_price", str(config.DEFAULT_PRO_PRICE_USD))
    biz_price = await db.get_bot_setting("business_price", str(config.DEFAULT_BUSINESS_PRICE_USD))
    text = await _plans_text(db)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=plans_kb(f"${pro_price}", f"${biz_price}", support_username=await db.get_bot_setting("support_username", "")),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=plans_kb(f"${pro_price}", f"${biz_price}", support_username=await db.get_bot_setting("support_username", "")),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("sub:select:"), StateFilter("*"))
async def cb_select_plan(callback: CallbackQuery, db: Database) -> None:
    plan = callback.data.split(":")[2]
    plan_info = PLAN_LIMITS.get(plan)
    if not plan_info:
        await callback.answer("Неизвестный план.", show_alert=True)
        return

    await callback.message.edit_text(
        f"{e('💳')} <b>Выберите способ оплаты</b>\n\n"
        f"Выбранный план: {plan_info['emoji']} <b>{plan_info['label']}</b> на 30 дней",
        reply_markup=payment_provider_kb(plan, support_username=await db.get_bot_setting("support_username", "")),
        parse_mode="HTML",
    )
    await callback.answer()


# ── PLATEGA ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:platega:"), StateFilter("*"))
async def cb_pay_platega(callback: CallbackQuery, db: Database) -> None:
    plan = callback.data.split(":")[2]
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка.", show_alert=True)
        return

    price_key = "pro_price" if plan == "pro" else "business_price"
    amount = float(await db.get_bot_setting(price_key, str(config.DEFAULT_PRO_PRICE_USD)))

    payment = await db.create_payment(user.id, "platega", plan, amount, "USD")
    order_id = f"dm_{user.id}_{payment.id}"

    await callback.message.edit_text(
        f"{e('⏳')} <b>Создаём платёж...</b>", parse_mode="HTML"
    )
    await callback.answer()

    result = await payment_platega.create_payment_link(
        order_id=order_id,
        amount=amount,
        currency="USD",
        description=f"DMautosender {plan.capitalize()} — 30 дней",
    )

    if not result:
        await db.update_payment_status(payment.id, "error")
        await callback.message.edit_text(
            f"{e('❌')} <b>Ошибка создания платежа.</b>\n\n"
            f"Платёжный провайдер недоступен. Попробуйте другой способ.",
            reply_markup=payment_provider_kb(plan, support_username=await db.get_bot_setting("support_username", "")),
            parse_mode="HTML",
        )
        return

    await db.update_payment_status(payment.id, "pending", result["payment_id"])
    plan_info = PLAN_LIMITS[plan]
    support = await db.get_bot_setting("support_username", "")
    await callback.message.edit_text(
        f"{e('💳')} <b>Оплата через Platega</b>\n\n"
        f"Plan: {plan_info['emoji']} <b>{plan_info['label']}</b>\n"
        f"Сумма: <b>${amount}</b>\n\n"
        f"Нажмите кнопку ниже для оплаты.\n"
        f"После оплаты нажмите <b>«Проверить оплату»</b>.",
        reply_markup=payment_waiting_kb(result["payment_url"], payment.id, "platega", support),
        parse_mode="HTML",
    )


# ── CRYPTOBOT ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:cryptobot:"), StateFilter("*"))
async def cb_pay_cryptobot(callback: CallbackQuery, db: Database) -> None:
    plan = callback.data.split(":")[2]
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка.", show_alert=True)
        return

    price_key = "pro_price" if plan == "pro" else "business_price"
    amount = float(await db.get_bot_setting(price_key, str(config.DEFAULT_PRO_PRICE_USD)))

    payment = await db.create_payment(user.id, "cryptobot", plan, amount, "USDT")

    await callback.message.edit_text(
        f"{e('⏳')} <b>Создаём инвойс...</b>", parse_mode="HTML"
    )
    await callback.answer()

    result = await payment_cryptobot.create_invoice(
        amount=amount,
        asset="USDT",
        description=f"DMautosender {plan.capitalize()} — 30 дней",
        payload=f"dm_{user.id}_{payment.id}",
    )

    if not result:
        await db.update_payment_status(payment.id, "error")
        await callback.message.edit_text(
            f"{e('❌')} <b>Ошибка создания инвойса.</b>\n\n"
            f"CryptoBot недоступен. Попробуйте другой способ.",
            reply_markup=payment_provider_kb(plan, support_username=await db.get_bot_setting("support_username", "")),
            parse_mode="HTML",
        )
        return

    await db.update_payment_status(payment.id, "pending", str(result["invoice_id"]))
    plan_info = PLAN_LIMITS[plan]
    support = await db.get_bot_setting("support_username", "")
    await callback.message.edit_text(
        f"{e('💠')} <b>Оплата через CryptoBot</b>\n\n"
        f"Plan: {plan_info['emoji']} <b>{plan_info['label']}</b>\n"
        f"Сумма: <b>{amount} USDT</b>\n\n"
        f"Откройте @CryptoBot для оплаты.\n"
        f"После оплаты нажмите <b>«Проверить оплату»</b>.",
        reply_markup=payment_waiting_kb(result["bot_invoice_url"], payment.id, "cryptobot", support),
        parse_mode="HTML",
    )


# ── TON ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:ton:"), StateFilter("*"))
async def cb_pay_ton(callback: CallbackQuery, db: Database) -> None:
    plan = callback.data.split(":")[2]
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка.", show_alert=True)
        return

    if not config.TON_WALLET:
        await callback.answer("TON-платежи не настроены.", show_alert=True)
        return

    price_key = "pro_price" if plan == "pro" else "business_price"
    amount_usd = float(await db.get_bot_setting(price_key, str(config.DEFAULT_PRO_PRICE_USD)))
    # Approximate TON price (can be fetched from TonCenter, here using fixed)
    ton_rate = float(await db.get_bot_setting("ton_rate_usd", "3.0"))
    amount_ton = round(amount_usd / ton_rate, 2)

    payment = await db.create_payment(user.id, "ton", plan, amount_ton, "TON")
    info = payment_toncenter.get_payment_info(user.id, payment.id, amount_ton)
    await db.update_payment_status(payment.id, "pending")

    plan_info = PLAN_LIMITS[plan]
    support = await db.get_bot_setting("support_username", "")
    await callback.message.edit_text(
        f"{e('💎')} <b>Оплата TON</b>\n\n"
        f"Plan: {plan_info['emoji']} <b>{plan_info['label']}</b>\n"
        f"Сумма: <b>{amount_ton} TON</b> (~${amount_usd})\n\n"
        f"{e('🔑')} <b>Адрес кошелька:</b>\n<code>{info['wallet']}</code>\n\n"
        f"{e('📝')} <b>Комментарий (обязательно!):</b>\n<code>{info['payload']}</code>\n\n"
        f"{e('⚠️')} Без комментария платёж не будет засчитан!\n\n"
        f"После отправки нажмите <b>«Проверить оплату»</b>.",
        reply_markup=ton_payment_kb(info["deeplink"], info["tonkeeper_url"], payment.id, support),
        parse_mode="HTML",
    )
    await callback.answer()


# ── PAYMENT CHECK ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:check:"), StateFilter("*"))
async def cb_check_payment(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    parts = callback.data.split(":")
    provider = parts[2]
    payment_db_id = int(parts[3])

    payment = await db.get_payment(payment_db_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user or payment.user_id != user.id:
        await callback.answer("Ошибка доступа.", show_alert=True)
        return

    await callback.answer("🔄 Проверяем статус...")

    paid = False

    if provider == "platega" and payment.external_id:
        status = await payment_platega.check_payment_status(payment.external_id)
        paid = (status == "paid")

    elif provider == "cryptobot" and payment.external_id:
        status = await payment_cryptobot.check_invoice_status(int(payment.external_id))
        paid = (status == "paid")

    elif provider == "ton":
        paid = await payment_toncenter.check_transaction_received(
            user.id, payment_db_id, payment.amount
        )

    if paid:
        await db.update_payment_status(payment_db_id, "paid")
        # Create subscription
        sub = await db.create_subscription(
            user_id=user.id,
            plan=payment.plan,
            duration_days=PLAN_DURATION_DAYS,
            provider=provider,
            payment_id=str(payment_db_id),
        )
        
        # Referral payout (10%)
        if user.referrer_id:
            reward = payment.amount * 0.10
            await db.add_balance(user.referrer_id, reward)
            try:
                referrer = await db.get_user_by_id(user.referrer_id)
                if referrer:
                    await bot.send_message(
                        referrer.tg_id,
                        f"🎉 <b>Отличные новости!</b>\n\n"
                        f"Ваш реферал оплатил подписку. Вам начислено 10%: <b>${reward:.2f}</b>\n"
                        f"Текущий баланс доступен в Профиле.",
                        parse_mode="HTML"
                    )
            except Exception as e:
                pass
        plan_info = PLAN_LIMITS.get(payment.plan, {})
        label = plan_info.get("label", payment.plan.upper())
        emoji = plan_info.get("emoji", "⭐")
        await callback.message.edit_text(
            f"{e('✅')} <b>Оплата подтверждена!</b>\n\n"
            f"Активирован план: {emoji} <b>{label}</b>\n"
            f"{e('⏰')} Действует до: <b>{sub.expires_at[:10]}</b>\n\n"
            f"{e('🚀')} Добро пожаловать! Можете начинать работу.",
            reply_markup=payment_success_kb(),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"{e('⏳')} <b>Платёж ещё не подтверждён.</b>\n\n"
            f"Если вы уже оплатили — подождите несколько минут и проверьте снова.\n"
            f"Платежи обрабатываются в течение 1-5 минут.",
            reply_markup=callback.message.reply_markup,
            parse_mode="HTML",
        )
