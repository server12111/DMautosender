"""Admin settings — support username, plan prices, payment keys."""
import logging
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...keyboards.inline import admin_settings_kb, admin_back_kb, cancel_kb
from ...database.db import Database
from ...utils.emoji import e
from ...config import config

logger = logging.getLogger("dmsender.admin.settings")
router = Router()


class AdminSettingsStates(StatesGroup):
    waiting_value = State()


SETTING_LABELS = {
    "support_username": "💬 Support username (без @)",
    "pro_price": "💵 Цена Pro (USD, например: 9.99)",
    "business_price": "💵 Цена Business (USD, например: 29.99)",
    "platega_merchant": "🔑 Platega MerchantId",
    "platega_secret": "🔑 Platega Secret key",
    "cryptobot_token": "🔑 CryptoBot API Token",
    "ton_wallet": "💎 TON Wallet адрес",
    "ton_rate_usd": "💱 Курс TON/USD (например: 3.5)",
}

# Maps admin setting keys to config/DB keys
SETTING_DB_KEYS = {
    "support_username": "support_username",
    "pro_price": "pro_price",
    "business_price": "business_price",
    "platega_merchant": "platega_merchant_id",
    "platega_secret": "platega_secret",
    "cryptobot_token": "cryptobot_token",
    "ton_wallet": "ton_wallet",
    "ton_rate_usd": "ton_rate_usd",
}


async def _settings_text(db: Database) -> str:
    support = await db.get_bot_setting("support_username", "не задан")
    pro_price = await db.get_bot_setting("pro_price", str(config.DEFAULT_PRO_PRICE_USD))
    biz_price = await db.get_bot_setting("business_price", str(config.DEFAULT_BUSINESS_PRICE_USD))
    platega_ok = "✅" if await db.get_bot_setting("platega_merchant_id") else "❌"
    cryptobot_ok = "✅" if await db.get_bot_setting("cryptobot_token") else "❌"
    ton_ok = "✅" if await db.get_bot_setting("ton_wallet") else "❌"

    return (
        f"{e('⚙️')} <b>Настройки бота</b>\n\n"
        f"{e('💬')} Support: @{support}\n"
        f"{e('💵')} Pro: ${pro_price}/мес\n"
        f"{e('💵')} Business: ${biz_price}/мес\n\n"
        f"<b>Платёжные провайдеры:</b>\n"
        f"  Platega: {platega_ok}\n"
        f"  CryptoBot: {cryptobot_ok}\n"
        f"  TON: {ton_ok}"
    )


@router.callback_query(F.data == "admin:settings", StateFilter("*"))
async def cb_admin_settings(callback: CallbackQuery, db: Database) -> None:
    text = await _settings_text(db)
    try:
        await callback.message.edit_text(
            text, reply_markup=admin_settings_kb(), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=admin_settings_kb(), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:set:"), StateFilter("*"))
async def cb_set_setting(callback: CallbackQuery, state: FSMContext) -> None:
    setting_key = callback.data.split(":", 2)[2]
    label = SETTING_LABELS.get(setting_key, setting_key)

    await state.set_state(AdminSettingsStates.waiting_value)
    await state.update_data(setting_key=setting_key)

    await callback.message.edit_text(
        f"{e('✏️')} <b>Изменение настройки</b>\n\n"
        f"<b>{label}</b>\n\n"
        f"Введите новое значение:",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(AdminSettingsStates.waiting_value))
async def fsm_setting_value(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    setting_key = data.get("setting_key", "")
    value = (message.text or "").strip()

    if not value:
        await message.answer("❌ Значение не может быть пустым:", reply_markup=cancel_kb())
        return

    # Validate prices
    if setting_key in ("pro_price", "business_price", "ton_rate_usd"):
        try:
            float(value.replace(",", "."))
            value = value.replace(",", ".")
        except ValueError:
            await message.answer("❌ Введите число (например: 9.99):", reply_markup=cancel_kb())
            return

    db_key = SETTING_DB_KEYS.get(setting_key, setting_key)
    await db.set_bot_setting(db_key, value)
    await state.clear()

    label = SETTING_LABELS.get(setting_key, setting_key)
    text = await _settings_text(db)
    await message.answer(
        f"{e('✅')} <b>{label}</b> обновлено!\n\n{text}",
        reply_markup=admin_settings_kb(),
        parse_mode="HTML",
    )
