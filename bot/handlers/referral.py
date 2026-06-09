import logging
from aiogram.filters import StateFilter
from aiogram import Router, F
from aiogram.types import CallbackQuery
from ..database.db import Database
from ..keyboards.inline import profile_kb

router = Router()

@router.callback_query(F.data == "profile:withdraw", StateFilter("*"))
async def cb_withdraw(callback: CallbackQuery, db: Database) -> None:
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        return
        
    if user.balance < 5.0:
        await callback.answer(f"❌ Минимальная сумма для вывода $5. Ваш баланс: ${user.balance:.2f}", show_alert=True)
        return
        
    support_username = await db.get_bot_setting("support_username", "")
    await callback.answer(f"✅ У вас ${user.balance:.2f}. Напишите в поддержку для вывода средств: {support_username}", show_alert=True)