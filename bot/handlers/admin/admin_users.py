"""Admin user management — list, view, ban/unban, manual subscription grant."""
import logging
from aiogram.filters import StateFilter
from aiogram import Router, F
from aiogram.types import CallbackQuery

from ...keyboards.inline import admin_users_kb, admin_user_actions_kb, admin_back_kb
from ...database.db import Database
from ...utils.emoji import e

logger = logging.getLogger("dmsender.admin.users")
router = Router()

PER_PAGE = 10


@router.callback_query(F.data.startswith("admin:users:"), StateFilter("*"))
async def cb_admin_users(callback: CallbackQuery, db: Database) -> None:
    page = int(callback.data.split(":")[2])
    total = await db.count_users()
    users = await db.get_all_users(limit=PER_PAGE, offset=page * PER_PAGE)

    text = (
        f"{e('👥')} <b>Пользователи</b> (всего: {total})\n\n"
        f"Страница {page + 1}/{max(1, (total + PER_PAGE - 1) // PER_PAGE)}"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=admin_users_kb(users, page, total, PER_PAGE),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=admin_users_kb(users, page, total, PER_PAGE),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:"), StateFilter("*"))
async def cb_admin_user_view(callback: CallbackQuery, db: Database) -> None:
    user_id = int(callback.data.split(":")[2])
    user = await db.get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    sub = await db.get_active_subscription(user_id)
    plan = sub.plan if sub else "free"
    expires = sub.expires_at[:10] if sub and sub.expires_at else "—"
    ban_status = f"{e('⛔')} ЗАБЛОКИРОВАН" if user.is_banned else f"{e('🟢')} активен"
    uname = f"@{user.username}" if user.username else "нет"

    text = (
        f"{e('👤')} <b>Пользователь #{user_id}</b>\n\n"
        f"{e('🆔')} Telegram ID: <code>{user.tg_id}</code>\n"
        f"Username: {uname}\n"
        f"Имя: {user.full_name or '—'}\n"
        f"Регистрация: {user.created_at[:10] if user.created_at else '—'}\n"
        f"Статус: {ban_status}\n\n"
        f"{e('💳')} Подписка: <b>{plan.upper()}</b>\n"
        f"{e('⏰')} Истекает: {expires}"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=admin_user_actions_kb(user_id, bool(user.is_banned)),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=admin_user_actions_kb(user_id, bool(user.is_banned)),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:toggle_ban:"), StateFilter("*"))
async def cb_toggle_ban_user(callback: CallbackQuery, db: Database) -> None:
    user_id = int(callback.data.split(":")[3])
    user = await db.get_user_by_id(user_id)
    if user:
        await db.ban_user(user_id, not user.is_banned)
        status = "заблокирован" if not user.is_banned else "разблокирован"
        await callback.answer(f"Пользователь #{user_id} {status}.", show_alert=True)
    # Refresh view
    callback.data = f"admin:user:{user_id}"
    await cb_admin_user_view(callback, db)



@router.callback_query(F.data.startswith("admin:grant:"), StateFilter("*"))
async def cb_grant_subscription(callback: CallbackQuery, db: Database) -> None:
    # admin:grant:plan:days:user_id
    parts = callback.data.split(":")
    plan = parts[2]
    days = int(parts[3])
    user_id = int(parts[4])

    user = await db.get_user_by_id(user_id)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    sub = await db.create_subscription(
        user_id=user_id,
        plan=plan,
        duration_days=days,
        provider="admin",
        payment_id=f"admin:{callback.from_user.id}",
    )
    plan_info_labels = {"pro": "Pro ⭐", "business": "Business 💎"}
    label = plan_info_labels.get(plan, plan)
    await callback.answer(
        f"✅ Пользователю #{user_id} выдан {label} на {days} дней!",
        show_alert=True,
    )
    callback.data = f"admin:user:{user_id}"
    await cb_admin_user_view(callback, db)
