import logging
from ..utils.emoji import e
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..keyboards.inline import database_menu_kb, back_to_menu_kb, confirm_kb, cancel_kb
from ..database.db import Database
from ..database.models import BotUser, Campaign
from ..services.file_parser import parse_txt
from ..services.subscription import SubscriptionService

logger = logging.getLogger("dmsender.database")

router = Router()

class LoadDatabaseStates(StatesGroup):
    waiting_file = State()

def _format_db_text(campaign: Campaign, stats: dict) -> str:
    return (
        f"{e('📁')} <b>БАЗА ПОЛУЧАТЕЛЕЙ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('📢')} Рассылка: <b>{campaign.name}</b>\n\n"
        f"{e('📊')} <b>Статистика:</b>\n"
        f" ├ Всего загружено: <b>{stats['total']}</b>\n"
        f" ├ Успешно отправлено: <b>{stats['processed']}</b>\n"
        f" ├ В очереди: <b>{stats['remaining']}</b>\n"
        f" ├ Заблокировано: <b>{stats['blocked']}</b>\n"
        f" └ Ошибок: <b>{stats['errors']}</b>\n\n"
        f"<i>Выберите действие с базой:</i>"
    )

@router.callback_query(F.data.startswith("database:menu:"), StateFilter("*"))
async def cb_database_menu(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return await callback.answer("Рассылка не найдена", show_alert=True)
    stats = await db.get_targets_stats(campaign_id)
    try:
        await callback.message.edit_text(
            _format_db_text(campaign, stats), reply_markup=database_menu_kb(campaign_id, stats), parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("database:upload:"), StateFilter("*"))
async def cb_database_upload(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    await state.set_state(LoadDatabaseStates.waiting_file)
    await callback.message.edit_text(
        f"{e('📤')} <b>ЗАГРУЗКА БАЗЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Пожалуйста, отправьте <b>TXT файл</b> ИЛИ <b>введите текст</b> со списком пользователей в чат.\n\n"
        f"<i>{e('💡')} Допустимые форматы (каждый контакт с новой строки):</i>\n"
        " ├ <code>@username</code>\n"
        " ├ <code>123456789</code> (Telegram ID)\n"
        " └ <code>https://t.me/username</code>",
        reply_markup=cancel_kb(f"database:menu:{campaign_id}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(StateFilter(LoadDatabaseStates.waiting_file), F.document)
async def fsm_receive_file(
    message: Message, state: FSMContext, bot: Bot, db: Database, bot_user: BotUser = None
) -> None:
    user_id = bot_user.id if bot_user else 0
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()

    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        await message.answer(
            "❌ Пожалуйста, отправьте файл в формате <b>.txt</b>",
            reply_markup=cancel_kb(f"database:menu:{campaign_id}"), parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ Обрабатываем файл...")

    try:
        file = await bot.get_file(doc.file_id)
        from io import BytesIO
        buf = BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        buf.seek(0)
        content = buf.read()
    except Exception as e:
        await status_msg.edit_text(f"❌ Не удалось скачать файл: {e}", reply_markup=back_to_menu_kb())
        await state.clear()
        return

    identifiers = parse_txt(content)
    if not identifiers:
        await status_msg.edit_text(
            "⚠️ В файле не найдено корректных пользователей.\n\n"
            "Убедитесь, что формат правильный: @username или числовой ID.",
            reply_markup=back_to_menu_kb(),
        )
        await state.clear()
        return

    await status_msg.edit_text(f"⏳ Найдено {len(identifiers)} строк, проверяем лимиты...")

    svc = SubscriptionService(db)
    max_targets = await svc.get_limit(user_id, "max_targets")
    stats = await db.get_targets_stats(campaign_id)
    current_total = stats["total"]

    if max_targets != -1 and current_total + len(identifiers) > max_targets:
        allowed_to_add = max_targets - current_total
        if allowed_to_add <= 0:
            await status_msg.edit_text(
                f"❌ Достигнут лимит пользователей ({max_targets}).\n\n"
                f"Удалите старую базу или перейдите на тариф выше.",
                reply_markup=back_to_menu_kb(),
            )
            await state.clear()
            return
        
        identifiers = identifiers[:allowed_to_add]
        warning = f"\n⚠️ Загружено только {allowed_to_add} строк из-за ограничений вашего тарифа."
    else:
        warning = ""

    await status_msg.edit_text(f"⏳ Добавляем в базу {len(identifiers)}...")

    source_name = doc.file_name or "uploaded.txt"
    added, skipped = await db.add_targets_bulk(campaign_id, identifiers, source_name)
    await state.clear()

    campaign = await db.get_campaign(campaign_id)
    new_stats = await db.get_targets_stats(campaign_id)

    await status_msg.edit_text(
        f"✅ Файл успешно обработан!\n\n"
        f"Новых получателей добавлено: <b>{added}</b>\n"
        f"Дубликатов пропущено: <b>{skipped}</b>\n{warning}\n\n"
        + _format_db_text(campaign, new_stats),
        reply_markup=database_menu_kb(campaign_id, new_stats),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("database:clear_log:"), StateFilter("*"))
async def cb_db_clear_log(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await db.clear_send_log(campaign_id)
    campaign = await db.get_campaign(campaign_id)
    stats = await db.get_targets_stats(campaign_id)
    await callback.message.edit_text(
        "✅ История рассылок для базы очищена! (Статусы 'отправлено' сброшены)\n\n" + _format_db_text(campaign, stats),
        reply_markup=database_menu_kb(campaign_id, stats),
        parse_mode="HTML",
    )
    await callback.answer("История отправки очищена.")

@router.callback_query(F.data.startswith("database:clear_all:"), StateFilter("*"))
async def cb_db_clear_all_ask(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(clear_campaign_id=campaign_id)
    await callback.message.edit_text(
        "⚠️ <b>Внимание!</b>\n\n"
        "Вы действительно хотите полностью очистить базу получателей и историю этой рассылки?\n"
        "Это действие нельзя отменить.",
        reply_markup=confirm_kb("db_clear", f"database:menu:{campaign_id}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data == "confirm:db_clear", StateFilter("*"))
async def cb_db_clear_confirm(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("clear_campaign_id")
    if not campaign_id:
        return await callback.answer("Ошибка", show_alert=True)
        
    await db.clear_all_targets(campaign_id)
    await state.clear()
    
    campaign = await db.get_campaign(campaign_id)
    stats = await db.get_targets_stats(campaign_id)
    await callback.message.edit_text(
        "✅ База получателей и история очищены.\n\n" + _format_db_text(campaign, stats),
        reply_markup=database_menu_kb(campaign_id, stats),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(LoadDatabaseStates.waiting_file), F.text)
async def fsm_receive_text(
    message: Message, state: FSMContext, db: Database, bot_user: BotUser = None
) -> None:
    user_id = bot_user.id if bot_user else 0
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()

    content = message.text.encode('utf-8')
    status_msg = await message.answer("⏳ Обрабатываем введенный текст...")

    try:
        targets = parse_txt(content)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка обработки текста: {e}", reply_markup=back_to_menu_kb())
        await state.clear()
        return

    if not targets:
        await status_msg.edit_text(
            "❌ <b>Ошибка:</b> Не найдено валидных контактов.",
            reply_markup=back_to_menu_kb(), parse_mode="HTML"
        )
        return

    added, skipped = await db.add_targets_bulk(campaign_id, targets, "manual_input")
    stats = await db.get_targets_stats(campaign_id)
    await state.clear()

    try:
        await status_msg.edit_text(
            f"{e('✅')} <b>База успешно обновлена!</b>\n\n" \
            f"Добавлено контактов: <b>{len(targets)}</b>",
            reply_markup=database_menu_kb(campaign_id, stats),
            parse_mode="HTML"
        )
    except Exception:
        pass
