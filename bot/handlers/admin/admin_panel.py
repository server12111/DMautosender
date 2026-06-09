"""Admin main panel — entry point and statistics."""
import logging
from aiogram.filters import StateFilter
from aiogram import Router, F
from aiogram.types import CallbackQuery

from ...keyboards.inline import admin_panel_kb
from ...database.db import Database
from ...utils.emoji import e

logger = logging.getLogger("dmsender.admin.panel")
router = Router()


@router.callback_query(F.data == "admin:panel", StateFilter("*"))
async def cb_admin_panel(callback: CallbackQuery, db: Database) -> None:
    users_count = await db.count_users()
    subs_count = await db.count_active_subscriptions()
    all_payments = await db.get_all_payments(limit=1000)
    total_paid = sum(p.amount for p in all_payments if p.status == "paid")

    text = (
        f"{e('⚙️')} <b>Панель администратора</b>\n\n"
        f"{e('👥')} Пользователей: <b>{users_count}</b>\n"
        f"{e('💳')} Активных подписок: <b>{subs_count}</b>\n"
        f"{e('💰')} Оборот: <b>${total_paid:.2f}</b>\n\n"
        f"Выберите раздел:"
    )
    try:
        await callback.message.edit_text(
            text, reply_markup=admin_panel_kb(), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=admin_panel_kb(), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "admin:stats", StateFilter("*"))
async def cb_admin_stats(callback: CallbackQuery, db: Database) -> None:
    await cb_admin_panel(callback, db)


@router.callback_query(F.data == "admin:payments", StateFilter("*"))
async def cb_admin_payments(callback: CallbackQuery, db: Database) -> None:
    payments = await db.get_all_payments(limit=30)
    if not payments:
        text = f"{e('💳')} <b>Платежи</b>\n\n<i>Нет платежей.</i>"
    else:
        lines = [f"{e('💳')} <b>Последние 30 платежей</b>\n"]
        for p in payments:
            status_icon = "✅" if p.status == "paid" else ("⏳" if p.status == "pending" else "❌")
            date = p.created_at[:10] if p.created_at else "—"
            lines.append(
                f"{status_icon} #{p.id} | user:{p.user_id} | "
                f"{p.plan} | {p.amount}{p.currency} | {p.provider} | {date}"
            )
        text = "\n".join(lines)
    from ...keyboards.inline import admin_back_kb
    try:
        await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin:system_status", StateFilter("*"))
async def cb_admin_system_status(callback: CallbackQuery) -> None:
    import psutil
    from ...keyboards.inline import admin_back_kb
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    text = (
        f"{e('🖥')} <b>Статус системы</b>\n\n"
        f"CPU: {cpu}%\n"
        f"RAM: {mem.percent}% ({mem.used // 1048576}MB / {mem.total // 1048576}MB)\n"
        f"Disk: {disk.percent}%\n"
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()
