import logging
from ..utils.emoji import e
from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from ..database.db import Database
from ..database.models import BotUser
from ..keyboards.inline import campaigns_list_kb, campaign_menu_kb, campaign_accounts_kb, back_to_menu_kb

logger = logging.getLogger("dmsender.handlers.campaigns")

campaigns_router = Router()

async def edit_or_answer(callback, text, reply_markup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    await callback.answer()

class CampaignCreate(StatesGroup):
    name = State()

@campaigns_router.callback_query(F.data == "campaigns:list", StateFilter("*"))
async def show_campaigns_list(callback: types.CallbackQuery, db: Database, bot_user: BotUser = None) -> None:
    user_id = bot_user.id if bot_user else 0
    campaigns = await db.get_campaigns(user_id)
    
    text = f"{e('📢')} <b>ВАШИ РАССЫЛКИ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    if not campaigns:
        text += "У вас пока нет ни одной созданной рассылки.\n<i>Нажмите «{e('➕')} Создать рассылку», чтобы начать.</i>"
    else:
        text += "Выберите нужную рассылку для управления настройками или создайте новую."
        
    await edit_or_answer(callback, text, reply_markup=campaigns_list_kb(campaigns))


@campaigns_router.callback_query(F.data == "campaigns:create", StateFilter("*"))
async def process_create_campaign(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        f"{e('➕')} <b>СОЗДАНИЕ РАССЫЛКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Пожалуйста, введите удобное <b>название</b> для новой рассылки (например: <i>Клиенты за февраль</i>):",
        reply_markup=back_to_menu_kb()
    )
    await state.set_state(CampaignCreate.name)


@campaigns_router.message(CampaignCreate.name)
async def process_campaign_name(message: types.Message, state: FSMContext, db: Database, bot_user: BotUser = None) -> None:
    name = message.text.strip()
    if not name:
        return
        
    user_id = bot_user.id if bot_user else 0
    campaign_id = await db.create_campaign(user_id, name)
    await state.clear()
    
    campaign = await db.get_campaign(campaign_id)
    await message.answer(
        f"{e('✅')} Отлично! Рассылка <b>{campaign.name}</b> успешно создана!\n\n"
        f"<i>Теперь вы можете настроить текст сообщения, загрузить получателей и подключить аккаунты.</i>",
        reply_markup=campaign_menu_kb(campaign)
    )


@campaigns_router.callback_query(F.data.startswith("campaign:view:"), StateFilter("*"))
async def view_campaign(callback: types.CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        await callback.answer("Рассылка не найдена или удалена.", show_alert=True)
        return
        
    assigned_accs = len(await db.get_campaign_accounts(campaign_id))
    stats = await db.get_targets_stats(campaign_id)
    
    status_text = "🟢 Запущена" if campaign.status == "running" else ("⏸ Пауза" if campaign.status == "paused" else "🔴 Остановлена")
    
    text = (
        f"{e('📢')} <b>РАССЫЛКА:</b> {campaign.name}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('🔄')} <b>Статус:</b> {status_text}\n"
        f"{e('📱')} <b>Аккаунтов:</b> {assigned_accs}\n"
        f"{e('🎯')} <b>Получателей:</b> {stats['total']} чел.\n"
        f"{e('📝')} <b>Текст:</b> {'✅ Установлен' if campaign.text else '❌ Не задан'}\n\n"
        f"<i>Выберите нужное действие в меню ниже:</i>"
    )
    
    await edit_or_answer(callback, text, reply_markup=campaign_menu_kb(campaign))


@campaigns_router.callback_query(F.data.startswith("campaign:delete:"), StateFilter("*"))
async def delete_campaign_ask(callback: types.CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    from ..keyboards.inline import confirm_kb
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите удалить эту рассылку?\nЭто действие нельзя отменить.",
        reply_markup=confirm_kb(f"confirm:del_camp:{campaign_id}", f"campaign:view:{campaign_id}")
    )
    await callback.answer()

@campaigns_router.callback_query(F.data.startswith("confirm:del_camp:"), StateFilter("*"))
async def delete_campaign(callback: types.CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await db.delete_campaign(campaign_id)
    await callback.answer("✅ Рассылка удалена")
    await show_campaigns_list(callback, db)


@campaigns_router.callback_query(F.data.startswith("campaign:accounts:"), StateFilter("*"))
async def manage_campaign_accounts(callback: types.CallbackQuery, db: Database, bot_user: BotUser = None) -> None:
    campaign_id = int(callback.data.split(":")[2])
    user_id = bot_user.id if bot_user else 0
    
    user_accounts = await db.get_active_accounts(user_id)
    assigned_account_ids = await db.get_campaign_accounts(campaign_id)
    
    text = (
        f"{e('📱')} <b>АККАУНТЫ ДЛЯ РАССЫЛКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Отметьте галочками {e('✅')} те аккаунты, которые будут участвовать в отправке сообщений для этой рассылки.</i>"
    )
    
    await edit_or_answer(callback, text, reply_markup=campaign_accounts_kb(campaign_id, user_accounts, assigned_account_ids))


@campaigns_router.callback_query(F.data.startswith("campaign:toggle_acc:"), StateFilter("*"))
async def toggle_campaign_account(callback: types.CallbackQuery, db: Database) -> None:
    _, _, campaign_id_str, account_id_str = callback.data.split(":")
    campaign_id = int(campaign_id_str)
    account_id = int(account_id_str)
    
    assigned_account_ids = await db.get_campaign_accounts(campaign_id)
    
    if account_id in assigned_account_ids:
        await db.remove_account_from_campaign(campaign_id, account_id)
    else:
        await db.assign_account_to_campaign(campaign_id, account_id)
        
    await manage_campaign_accounts(callback, db)
