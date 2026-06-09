import logging
from ..utils.emoji import e
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..keyboards.inline import delays_menu_kb, cancel_kb
from ..database.db import Database
from ..database.models import Campaign

logger = logging.getLogger("dmsender.delays")

router = Router()

class DelaysStates(StatesGroup):
    waiting_fixed = State()
    waiting_min = State()
    waiting_max = State()
    waiting_pause = State()

def _delays_text(c: Campaign) -> str:
    mode = "Фиксированная" if c.delay_mode == "fixed" else "Случайная"
    if c.delay_mode == "fixed":
        delay_info = f" ├ <b>Задержка:</b> {c.delay_fixed}с"
    else:
        delay_info = f" ├ <b>Диапазон:</b> от {c.delay_min}с до {c.delay_max}с"
    pause = f"{c.pause_cycles}с" if c.pause_cycles > 0 else "нет"
    return (
        f"{e('⏱')} <b>НАСТРОЙКИ ЗАДЕРЖЕК</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('📢')} Рассылка: <b>{c.name}</b>\n\n"
        f"{e('⚙️')} <b>Параметры:</b>\n"
        f" ├ <b>Режим:</b> {mode}\n"
        f"{delay_info}\n"
        f" └ <b>Пауза (между циклами):</b> {pause}\n\n"
        f"<i>{e('⚠️')} Рекомендуется ставить паузы для снижения риска блокировки аккаунтов.</i>"
    )

@router.callback_query(F.data.startswith("delays:menu:"), StateFilter("*"))
async def cb_delays_menu(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return await callback.answer("Рассылка не найдена", show_alert=True)
    await callback.message.edit_text(
        _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, campaign.delay_mode),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delays:set_fixed:"), StateFilter("*"))
async def cb_set_fixed_mode(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    await db.update_campaign_delays(
        campaign_id, "fixed", campaign.delay_fixed, campaign.delay_min, campaign.delay_max, campaign.pause_cycles
    )
    campaign.delay_mode = "fixed"
    await callback.message.edit_text(
        "✅ Режим фиксированной задержки.\n\n" + _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, "fixed"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delays:set_random:"), StateFilter("*"))
async def cb_set_random_mode(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    await db.update_campaign_delays(
        campaign_id, "random", campaign.delay_fixed, campaign.delay_min, campaign.delay_max, campaign.pause_cycles
    )
    campaign.delay_mode = "random"
    await callback.message.edit_text(
        "✅ Режим случайной задержки.\n\n" + _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, "random"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("delays:edit:"), StateFilter("*"))
async def cb_edit_delay(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    campaign = await db.get_campaign(campaign_id)
    
    if campaign.delay_mode == "fixed":
        await state.set_state(DelaysStates.waiting_fixed)
        await callback.message.edit_text(
            f"{e('⏱')} Введите задержку в секундах (текущее: {campaign.delay_fixed}с):\n"
            f"Минимум: 1, рекомендуется 10-30 для безопасности.",
            reply_markup=cancel_kb(f"campaign:view:{campaign_id}"),
        )
    else:
        await state.set_state(DelaysStates.waiting_min)
        await callback.message.edit_text(
            f"🎲 Введите МИНИМАЛЬНУЮ задержку в секундах (текущее: {campaign.delay_min}с):",
            reply_markup=cancel_kb(f"campaign:view:{campaign_id}"),
        )
    await callback.answer()

@router.message(StateFilter(DelaysStates.waiting_fixed))
async def fsm_delay_fixed(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()
        
    text = message.text.strip().replace(",", ".")
    
    if "-" in text:
        parts = text.split("-")
        if len(parts) == 2:
            try:
                min_val = float(parts[0].strip())
                max_val = float(parts[1].strip())
                if min_val > 0 and max_val > 0 and min_val < max_val:
                    campaign = await db.get_campaign(campaign_id)
                    await db.update_campaign_delays(
                        campaign_id, "random", campaign.delay_fixed, min_val, max_val, campaign.pause_cycles
                    )
                    await state.clear()
                    campaign = await db.get_campaign(campaign_id)
                    await message.answer(
                        "✅ Случайная задержка сохранена!\n\n" + _delays_text(campaign),
                        reply_markup=delays_menu_kb(campaign_id, "random"),
                        parse_mode="HTML",
                    )
                    return
            except ValueError:
                pass

    try:
        val = float(text)
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Пожалуйста, введите положительное число (например, 15) или диапазон (например, 10-20):", reply_markup=cancel_kb(f"campaign:view:{campaign_id}"))
    
    campaign = await db.get_campaign(campaign_id)
    await db.update_campaign_delays(
        campaign_id, campaign.delay_mode, val, campaign.delay_min, campaign.delay_max, campaign.pause_cycles
    )
    campaign.delay_fixed = val
    await state.clear()
    
    await message.answer(
        "✅ Задержка сохранена!\n\n" + _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, campaign.delay_mode),
        parse_mode="HTML",
    )


@router.message(StateFilter(DelaysStates.waiting_min))
async def fsm_delay_min(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Пожалуйста, введите положительное число:", reply_markup=cancel_kb(f"campaign:view:{campaign_id}"))
        
    await state.update_data(min_val=val)
    await state.set_state(DelaysStates.waiting_max)
    
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    campaign = await db.get_campaign(campaign_id)
    
    await message.answer(
        f"Теперь введите МАКСИМАЛЬНУЮ задержку (текущее: {campaign.delay_max}с).\n"
        f"Она должна быть больше {val}с:",
        reply_markup=cancel_kb(f"campaign:view:{campaign_id}"),
    )

@router.message(StateFilter(DelaysStates.waiting_max))
async def fsm_delay_max(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    min_val = data.get("min_val", 5.0)
    campaign_id = data.get("campaign_id")
    
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= min_val:
            return await message.answer(f"❌ Максимальная задержка должна быть больше минимальной ({min_val}). Повторите ввод:", reply_markup=cancel_kb(f"campaign:view:{campaign_id}"))
    except ValueError:
        return await message.answer("❌ Введите корректное число:", reply_markup=cancel_kb(f"campaign:view:{campaign_id}"))
        
    campaign = await db.get_campaign(campaign_id)
    await db.update_campaign_delays(
        campaign_id, campaign.delay_mode, campaign.delay_fixed, min_val, val, campaign.pause_cycles
    )
    campaign.delay_min = min_val
    campaign.delay_max = val
    await state.clear()
    
    await message.answer(
        "✅ Диапазон сохранён!\n\n" + _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, campaign.delay_mode),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("delays:set_pause:"), StateFilter("*"))
async def cb_set_pause(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    campaign = await db.get_campaign(campaign_id)
    await state.set_state(DelaysStates.waiting_pause)
    await callback.message.edit_text(
        f"⏸ Введите паузу (в секундах) между полными циклами отправки.\n"
        f"(Текущее: {campaign.pause_cycles}с)\n\n"
        f"Введите 0, чтобы отключить паузу.",
        reply_markup=cancel_kb(f"campaign:view:{campaign_id}"),
    )
    await callback.answer()

@router.message(StateFilter(DelaysStates.waiting_pause))
async def fsm_delay_pause(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()
        
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("❌ Введите положительное число или 0:", reply_markup=cancel_kb(f"campaign:view:{campaign_id}"))
        
    campaign = await db.get_campaign(campaign_id)
    await db.update_campaign_delays(
        campaign_id, campaign.delay_mode, campaign.delay_fixed, campaign.delay_min, campaign.delay_max, val
    )
    campaign.pause_cycles = val
    await state.clear()
    
    await message.answer(
        "✅ Пауза сохранена!\n\n" + _delays_text(campaign),
        reply_markup=delays_menu_kb(campaign_id, campaign.delay_mode),
        parse_mode="HTML",
    )
