"""Admin promo code management — list, create, deactivate."""
import logging
import random
import string
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...keyboards.inline import (
    admin_promos_kb, admin_promo_actions_kb, admin_back_kb, cancel_kb
)
from ...database.db import Database
from ...utils.emoji import e

logger = logging.getLogger("dmsender.admin.promo")
router = Router()


class PromoCreateStates(StatesGroup):
    plan = State()
    duration = State()
    max_uses = State()
    custom_code = State()


def _random_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


@router.callback_query(F.data == "admin:promo:list", StateFilter("*"))
async def cb_promo_list(callback: CallbackQuery, db: Database) -> None:
    promos = await db.get_all_promos()
    text = f"{e('🎟')} <b>Промокоды</b> (всего: {len(promos)})\n"
    if not promos:
        text += "\n<i>Промокодов нет. Создайте первый!</i>"
    try:
        await callback.message.edit_text(
            text, reply_markup=admin_promos_kb(promos), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=admin_promos_kb(promos), parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:promo:view:"), StateFilter("*"))
async def cb_promo_view(callback: CallbackQuery, db: Database) -> None:
    promo_id = int(callback.data.split(":")[3])
    promos = await db.get_all_promos()
    promo = next((p for p in promos if p.id == promo_id), None)
    if not promo:
        await callback.answer("Промокод не найден.", show_alert=True)
        return

    status = f"{e('✅')} Активен" if promo.is_active else f"{e('❌')} Деактивирован"
    plan_icons = {"pro": "⭐ Pro", "business": "💎 Business"}
    plan_label = plan_icons.get(promo.plan, promo.plan)

    text = (
        f"{e('🎟')} <b>Промокод: {promo.code}</b>\n\n"
        f"Статус: {status}\n"
        f"План: <b>{plan_label}</b>\n"
        f"Срок: <b>{promo.duration_days} дней</b>\n"
        f"Использований: <b>{promo.used_count}/{promo.max_uses}</b>\n"
        f"Истекает: {promo.expires_at[:10] if promo.expires_at else 'Нет'}\n"
        f"Создан: {promo.created_at[:10] if promo.created_at else '—'}"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=admin_promo_actions_kb(promo_id, bool(promo.is_active)),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=admin_promo_actions_kb(promo_id, bool(promo.is_active)),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:promo:toggle:"), StateFilter("*"))
async def cb_promo_toggle(callback: CallbackQuery, db: Database) -> None:
    promo_id = int(callback.data.split(":")[3])
    await db._conn.execute("UPDATE promo_codes SET is_active = NOT is_active WHERE id=?", (promo_id,))
    await db._conn.commit()
    await callback.answer("🔄 Статус промокода изменен.", show_alert=True)
    callback.data = f"admin:promo:view:{promo_id}"
    await cb_promo_view(callback, db)


@router.callback_query(F.data.startswith("admin:promo:delete:"), StateFilter("*"))
async def cb_promo_delete(callback: CallbackQuery, db: Database) -> None:
    promo_id = int(callback.data.split(":")[3])
    await db._conn.execute("DELETE FROM promo_activations WHERE promo_id=?", (promo_id,))
    await db._conn.execute("DELETE FROM promo_codes WHERE id=?", (promo_id,))
    await db._conn.commit()
    await callback.answer("❌ Промокод удален.", show_alert=True)
    callback.data = "admin:promo:list"
    await cb_promo_list(callback, db)


# ── Create promo FSM ───────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:promo:create", StateFilter("*"))
async def cb_promo_create(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoCreateStates.plan)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⭐ Pro", callback_data="promo_plan:pro"),
        InlineKeyboardButton(text="💎 Business", callback_data="promo_plan:business"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:promo:list"))
    await callback.message.edit_text(
        f"{e('🎟')} <b>Создание промокода</b>\n\n"
        f"Шаг 1/4. Выберите <b>план</b>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("promo_plan:"), StateFilter("*"), StateFilter(PromoCreateStates.plan))
async def fsm_promo_plan(callback: CallbackQuery, state: FSMContext) -> None:
    plan = callback.data.split(":")[1]
    await state.update_data(plan=plan)
    await state.set_state(PromoCreateStates.duration)
    await callback.message.edit_text(
        f"{e('🎟')} <b>Создание промокода</b>\n\n"
        f"Шаг 2/4. Введите <b>срок действия в днях</b>:\n"
        f"Например: <code>30</code> (один месяц), <code>7</code> (неделя)",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(PromoCreateStates.duration))
async def fsm_promo_duration(message: Message, state: FSMContext) -> None:
    try:
        days = int(message.text.strip())
        if days < 1 or days > 3650:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введите число от 1 до 3650:", reply_markup=cancel_kb())
        return
    await state.update_data(duration_days=days)
    await state.set_state(PromoCreateStates.max_uses)
    await message.answer(
        f"{e('🎟')} Шаг 3/4. Сколько раз можно использовать?\n"
        f"Например: <code>1</code> — одноразовый, <code>100</code> — 100 активаций",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )


@router.message(StateFilter(PromoCreateStates.max_uses))
async def fsm_promo_max_uses(message: Message, state: FSMContext) -> None:
    try:
        max_uses = int(message.text.strip())
        if max_uses < 1:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("❌ Введите число ≥ 1:", reply_markup=cancel_kb())
        return
    await state.update_data(max_uses=max_uses)
    await state.set_state(PromoCreateStates.custom_code)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎲 Сгенерировать автоматически", callback_data="promo_code:auto"
        )
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin:promo:list"))
    await message.answer(
        f"{e('🎟')} Шаг 4/4. Введите <b>код промокода</b> или нажмите «Сгенерировать»:\n"
        f"<i>Только буквы и цифры, от 4 до 20 символов</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "promo_code:auto", StateFilter(PromoCreateStates.custom_code))
async def fsm_promo_auto_code(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    code = _random_code(8)
    await _finish_promo_create(callback.message, state, db, code, callback.from_user.id)
    await callback.answer()


@router.message(StateFilter(PromoCreateStates.custom_code))
async def fsm_promo_custom_code(message: Message, state: FSMContext, db: Database) -> None:
    code = (message.text or "").strip().upper()
    if not code.replace("-", "").isalnum() or len(code) < 4 or len(code) > 20:
        await message.answer(
            "❌ Код должен содержать только буквы/цифры, 4-20 символов:",
            reply_markup=cancel_kb(),
        )
        return
    await _finish_promo_create(message, state, db, code, message.from_user.id)


async def _finish_promo_create(
    message_or_msg, state: FSMContext, db: Database, code: str, admin_tg_id: int
) -> None:
    data = await state.get_data()
    await state.clear()
    plan = data.get("plan", "pro")
    duration_days = data.get("duration_days", 30)
    max_uses = data.get("max_uses", 1)

    try:
        promo = await db.create_promo(
            code=code,
            plan=plan,
            duration_days=duration_days,
            max_uses=max_uses,
            created_by=admin_tg_id,
        )
        plan_icon = "⭐" if plan == "pro" else "💎"
        text = (
            f"{e('✅')} <b>Промокод создан!</b>\n\n"
            f"{e('🎟')} Код: <code>{promo.code}</code>\n"
            f"План: {plan_icon} {plan.capitalize()}\n"
            f"Срок: <b>{duration_days} дней</b>\n"
            f"Использований: <b>0/{max_uses}</b>"
        )
    except Exception as ex:
        text = f"❌ Ошибка создания промокода: {ex}"

    from ...keyboards.inline import admin_back_kb
    await message_or_msg.answer(text, reply_markup=admin_back_kb(), parse_mode="HTML")
