import logging
from ..utils.emoji import e
from aiogram import Router, F
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile

from ..database.db import Database
from ..userbot.manager import UserbotManager
from ..keyboards.inline import tools_menu_kb, cancel_kb, back_to_menu_kb
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ..keyboards.inline import _btn
from ..services.subscription import SubscriptionService
import re
import asyncio

logger = logging.getLogger("dmsender.tools")

router = Router()

class ToolsStates(StatesGroup):
    autoresponder_text = State()

class PhoneCheckerStates(StatesGroup):
    waiting_file = State()

class AdvancedParserStates(StatesGroup):
    waiting_source = State()
    waiting_mode = State()
    waiting_groups = State()
    waiting_file = State()

def _get_user_id(db_user, tg_id: int) -> int:
    return db_user.id if db_user else 0

@router.callback_query(F.data == "tools:menu", StateFilter("*"))
async def cb_tools_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        if callback.message.document:
            await callback.message.answer(
                f"{e('🧲')} <b>ИНСТРУМЕНТЫ</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Здесь собраны мощные утилиты для автоматизации и поиска целевой аудитории.\n\n"
                "<i>Выберите нужный инструмент ниже:</i>",
                reply_markup=tools_menu_kb(),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"{e('🧲')} <b>ИНСТРУМЕНТЫ</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Здесь собраны мощные утилиты для автоматизации и поиска целевой аудитории.\n\n"
                "<i>Выберите нужный инструмент ниже:</i>",
                reply_markup=tools_menu_kb(),
                parse_mode="HTML"
            )
    except Exception:
        pass

# ── Автоответчик ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "tools:autoresponder", StateFilter("*"))
async def cb_tools_autoresponder(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    db_user = await db.get_user_by_tg_id(callback.from_user.id)
    u_id = _get_user_id(db_user, callback.from_user.id)
    
    from ..services.subscription import SubscriptionService
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from ..keyboards.inline import _btn
    
    svc = SubscriptionService(db)
    sub = await svc.get_subscription(u_id)
    if not sub or sub.plan == "free":
        b = InlineKeyboardBuilder()
        b.row(_btn(text="💎 Купить подписку", callback_data="sub:plans", style="success"))
        b.row(_btn(text="❌ Отмена", callback_data="menu:cancel", style="danger"))
        await callback.message.edit_text(
            f"{e('❌')} <b>Доступ ограничен!</b>\n\n"
            "Автоответчик доступен только на подписках PRO и BUSINESS.",
            reply_markup=b.as_markup(),
            parse_mode="HTML"
        )
        return

    u_id = _get_user_id(db_user, callback.from_user.id)
    
    current_text = await db.get_setting(u_id, "autoresponder_text", "")
    is_on = await db.get_setting(u_id, "autoresponder_on", "0")
    
    status = "🟢 Включён" if is_on == "1" else "🔴 Выключен"
    
    msg = (
        f"{e('💬')} <b>АВТООТВЕТЧИК</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('🔄')} <b>Статус:</b> {status}\n\n"
        f"{e('📝')} <b>Текущий текст:</b>\n"
        f"<i>{current_text or 'Не задан'}</i>\n\n"
        f"{e('👇')} Отправьте в чат <b>новый текст</b> для автоответчика."
    )
    
    b = InlineKeyboardBuilder()
    if is_on == "1":
        b.row(_btn(text="🛑 Выключить", callback_data="tools:autoresponder:off"))
    else:
        if current_text:
            b.row(_btn(text="🟢 Включить", callback_data="tools:autoresponder:on"))
    b.row(_btn(text="Отмена", callback_data="tools:menu"))
    
    await state.set_state(ToolsStates.autoresponder_text)
    await callback.message.edit_text(msg, reply_markup=b.as_markup(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "tools:autoresponder:off", StateFilter("*"))
async def cb_autoresponder_off(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    db_user = await db.get_user_by_tg_id(callback.from_user.id)
    u_id = _get_user_id(db_user, callback.from_user.id)
    await db.set_setting(u_id, "autoresponder_on", "0")
    # Redirect back to the autoresponder menu
    await cb_tools_autoresponder(callback, state, db)

@router.callback_query(F.data == "tools:autoresponder:on", StateFilter("*"))
async def cb_autoresponder_on(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    db_user = await db.get_user_by_tg_id(callback.from_user.id)
    u_id = _get_user_id(db_user, callback.from_user.id)
    await db.set_setting(u_id, "autoresponder_on", "1")
    # Redirect back to the autoresponder menu
    await cb_tools_autoresponder(callback, state, db)

@router.message(Command("stop_auto", ignore_case=True), StateFilter(ToolsStates.autoresponder_text))
async def cmd_stop_auto(message: Message, state: FSMContext, db: Database) -> None:
    db_user = await db.get_user_by_tg_id(message.from_user.id)
    u_id = _get_user_id(db_user, message.from_user.id)
    await db.set_setting(u_id, "autoresponder_on", "0")
    await state.clear()
    await message.answer(f"{e('✅')} Автоответчик выключен.", reply_markup=tools_menu_kb())

@router.callback_query(F.data == "tools:checker", StateFilter("*"))
async def cb_tools_checker(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    db_user = await db.get_user_by_tg_id(callback.from_user.id)
    u_id = _get_user_id(db_user, callback.from_user.id)
    
    from ..services.subscription import SubscriptionService
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from ..keyboards.inline import _btn, cancel_kb
    
    svc = SubscriptionService(db)
    sub = await svc.get_subscription(u_id)
    if not sub or sub.plan == "free":
        b = InlineKeyboardBuilder()
        b.row(_btn(text="💎 Купить подписку", callback_data="sub:plans", style="success"))
        b.row(_btn(text="❌ Отмена", callback_data="menu:cancel", style="danger"))
        await callback.message.edit_text(
            f"{e('❌')} <b>Доступ ограничен!</b>\n\n"
            "Чекер номеров доступен только на подписках PRO и BUSINESS.",
            reply_markup=b.as_markup(),
            parse_mode="HTML"
        )
        return

    accounts = await db.get_all_active_accounts()
    if not accounts:
        await callback.message.edit_text(f"{e('❌')} Нет подключённых аккаунтов.", reply_markup=tools_menu_kb())
        return

    await state.update_data(checker_account_id=accounts[0].id)
    await state.set_state(PhoneCheckerStates.waiting_file)
    
    msg = (
        f"{e('📱')} <b>ЧЕКЕР НОМЕРОВ (PRO)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Отправьте <b>.txt файл</b> со списком номеров телефонов (каждый с новой строки).\n\n"
        f"<i>{e('💡')} Формат любой (например: +79991234567, 79991234567). Бот сверит базу и выдаст список юзернеймов/ID зарегистрированных пользователей Telegram.</i>"
    )
    await callback.message.edit_text(msg, reply_markup=cancel_kb("tools:parser"), parse_mode="HTML")
    await callback.answer()

@router.message(F.document, StateFilter(PhoneCheckerStates.waiting_file))
async def fsm_checker_file(message: Message, state: FSMContext, db: Database, manager: UserbotManager) -> None:
    if not message.document.file_name.endswith('.txt'):
        from ..keyboards.inline import cancel_kb
        await message.answer(f"{e('❌')} Пожалуйста, отправьте именно .txt файл.", reply_markup=cancel_kb("tools:parser"))
        return
        
    import os
    import tempfile
    bot = message.bot
    file_id = message.document.file_id
    file_info = await bot.get_file(file_id)
    
    fd, path = tempfile.mkstemp()
    os.close(fd)
    
    try:
        await bot.download_file(file_info.file_path, destination=path)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        phones_list = []
        for line in content.split('\n'):
            line = line.strip()
            if line:
                phones_list.append(line)
                
    except Exception as exc:
        from ..keyboards.inline import cancel_kb
        logger.error(f"Error reading phones file: {exc}")
        await message.answer(f"{e('❌')} Ошибка при чтении файла.", reply_markup=cancel_kb("tools:parser"))
        return
    finally:
        try: os.remove(path)
        except: pass
        
    if not phones_list:
        from ..keyboards.inline import cancel_kb
        await message.answer(f"{e('❌')} Файл пуст.", reply_markup=cancel_kb("tools:parser"))
        return
        
    data = await state.get_data()
    account_id = data.get("checker_account_id")
    
    status_msg = await message.answer(f"⏳ Подготовка к проверке базы ({len(phones_list)} номеров)...")
    await state.clear()
    
    import asyncio
    asyncio.create_task(manager.check_phones(account_id, phones_list, status_msg, message.from_user.id))

@router.message(StateFilter(ToolsStates.autoresponder_text))
async def fsm_autoresponder_text(message: Message, state: FSMContext, db: Database) -> None:
    db_user = await db.get_user_by_tg_id(message.from_user.id)
    u_id = _get_user_id(db_user, message.from_user.id)
    text = message.text or message.caption or ""
    if not text:
        await message.answer(f"{e('❌')} Пожалуйста, отправьте текстовое сообщение.", reply_markup=cancel_kb("tools:parser"))
        return
    
    await db.set_setting(u_id, "autoresponder_text", text)
    await db.set_setting(u_id, "autoresponder_on", "1")
    await state.clear()
    
    await message.answer(
        f"{e('✅')} <b>Автоответчик включён!</b>\n\n"
        "Теперь на первое сообщение от любого пользователя ваши аккаунты ответят этим текстом.",
        reply_markup=tools_menu_kb(),
        parse_mode="HTML"
    )

# ── Парсер ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "tools:parser", StateFilter("*"))
async def cb_tools_parser(callback: CallbackQuery, state: FSMContext, db: Database, manager: UserbotManager, camp_id: int = None) -> None:
    if camp_id is None:
        data = await state.get_data()
        camp_id = data.get("target_campaign_id")
    await state.clear()
    if camp_id:
        await state.update_data(target_campaign_id=camp_id)

    user_id = callback.from_user.id
    db_user = await db.get_user_by_tg_id(user_id)
    u_id = _get_user_id(db_user, user_id)
    
    accounts = await db.get_all_accounts(u_id)
    connected_accounts = [acc for acc in accounts if manager.is_connected(acc.id)]
    
    if not connected_accounts:
        await callback.answer(f"{e('❌')} У вас нет подключенных аккаунтов для работы парсера. Добавьте аккаунт в разделе Мои аккаунты.", show_alert=True)
        return
        
    await state.update_data(parse_account_id=connected_accounts[0].id)
    await state.set_state(AdvancedParserStates.waiting_source)
    
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="💬 Из чатов / групп", callback_data="tools:parser:src:groups"))
    builder.row(_btn(text="📂 Из .txt файла (базы)", callback_data="tools:parser:src:file"))
    builder.row(_btn(text="🔍 Проверка номеров (Чекер)", callback_data="tools:checker"))
    if camp_id:
        builder.row(_btn(text="Назад", callback_data=f"campaign:view:{camp_id}"))
    else:
        builder.row(_btn(text="Назад", callback_data="tools:menu"))
        
    try:
        text = (
            f"{e('🛠')} <b>ПАРСЕР АУДИТОРИИ (PRO)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Выберите <b>источник</b> аудитории:\n\n"
            "💬 <b>Из чатов</b> — бот сам соберет участников нужных групп.\n"
            "📂 <b>Из .txt файла</b> — загрузите вашу базу (@username или ID), и бот отфильтрует ее."
        )
        if callback.message.document:
            await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data.startswith("tools:parser:src:"), StateFilter("*"))
async def cb_parser_source_selected(callback: CallbackQuery, state: FSMContext) -> None:
    src = callback.data.split(":")[3]
    await state.update_data(parse_source=src)
    await state.set_state(AdvancedParserStates.waiting_mode)
    
    builder = InlineKeyboardBuilder()
    if src != "file":
        builder.row(_btn(text="👥 Все пользователи", callback_data="tools:parser:mode:all"))
    builder.row(_btn(text="⭐ Только Premium", callback_data="tools:parser:mode:premium"))
    builder.row(_btn(text="💎 NFT-Подарки (Долго)", callback_data="tools:parser:mode:gifts"))
    builder.row(_btn(text="⭐ Premium + 💎 NFT-Подарки", callback_data="tools:parser:mode:premium_gifts"))
    data = await state.get_data()
    camp_id = data.get("target_campaign_id")
    if camp_id:
        builder.row(_btn(text="Назад", callback_data=f"tools:parser:camp:{camp_id}"))
    else:
        builder.row(_btn(text="Назад", callback_data="tools:parser"))
        
    try:
        await callback.message.edit_text(
            f"{e('🛠')} <b>ПАРСЕР АУДИТОРИИ (PRO)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Выберите <b>режим</b> фильтрации:\n\n"
            "<i>⚠️ Внимание: режимы с поиском NFT работают медленнее из-за лимитов Telegram.</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        pass

@router.callback_query(F.data.startswith("tools:parser:mode:"), StateFilter("*"))
async def cb_parser_mode_selected(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    mode = callback.data.split(":")[3]
    await state.update_data(parse_mode=mode)
    
    src = data.get("parse_source", "groups")
    
    if src == "groups":
        await state.set_state(AdvancedParserStates.waiting_groups)
        try:
            await callback.message.edit_text(
                f"{e('🛠')} <b>ПАРСЕР АУДИТОРИИ (PRO)</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Отправьте ссылки на группы (каждая с новой строки или через пробел).\n"
                "Пример:\n"
                "<code>@group1\nhttps://t.me/group2\ngroup3</code>\n\n"
                f"<i>{e('💡')} Бот соберет участников без дубликатов и выдаст TXT-файл.</i>",
                reply_markup=cancel_kb("tools:parser"),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await state.set_state(AdvancedParserStates.waiting_file)
        try:
            await callback.message.edit_text(
                f"{e('🛠')} <b>ПАРСЕР АУДИТОРИИ (PRO)</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Отправьте <b>.txt файл</b> со списком пользователей (каждый @username или ID с новой строки).\n\n"
                f"<i>{e('💡')} Бот проверит каждого пользователя из вашего файла и выдаст отфильтрованный список.</i>",
                reply_markup=cancel_kb("tools:parser"),
                parse_mode="HTML"
            )
        except Exception:
            pass

@router.message(StateFilter(AdvancedParserStates.waiting_groups))
async def fsm_parser_groups(message: Message, state: FSMContext, db: Database, manager: UserbotManager) -> None:
    data = await state.get_data()
    user_id = message.from_user.id
    db_user = await db.get_user_by_tg_id(user_id)
    u_id = _get_user_id(db_user, user_id)
    
    # Check subscription right before starting
    svc = SubscriptionService(db)
    sub = await svc.get_subscription(u_id)
    if not sub or sub.plan == "free":
        b = InlineKeyboardBuilder()
        b.row(_btn(text="💎 Купить подписку", callback_data="sub:plans", style="success"))
        b.row(_btn(text="❌ Отмена", callback_data="menu:cancel", style="danger"))
        await message.answer(f"{e('❌')} <b>Доступ ограничен!</b>\n\nИнструмент «Парсер аудитории» доступен только на платных тарифах.\n\nПожалуйста, оформите подписку PRO или Business, чтобы использовать эту функцию.", reply_markup=b.as_markup(), parse_mode="HTML")
        return
        
    text = (message.text or "").strip()
    if not text:
        await message.answer(f"{e('❌')} Пожалуйста, отправьте список групп.", reply_markup=cancel_kb(f"tools:parser:camp:{data.get('target_campaign_id')}" if data.get("target_campaign_id") else "tools:parser"))
        return
        
    groups = re.split(r'[\s\n,]+', text)
    groups = [g for g in groups if g]
    
    if not groups:
        await message.answer(f"{e('❌')} Не найдено ни одной группы.", reply_markup=cancel_kb(f"tools:parser:camp:{data.get('target_campaign_id')}" if data.get("target_campaign_id") else "tools:parser"))
        return

    data = await state.get_data()
    account_id = data.get("parse_account_id")
    mode = data.get("parse_mode")
    
    status_msg = await message.answer("⏳ Подготовка к парсингу...")
    await state.clear()
    
    # We will pass the status_msg object to manager to update it directly
    campaign_id = data.get("campaign_id")
    asyncio.create_task(manager.advanced_parse_groups(account_id, groups, mode, status_msg, message.from_user.id, campaign_id=campaign_id))

@router.message(F.document, StateFilter(AdvancedParserStates.waiting_file))
async def fsm_parser_file(message: Message, state: FSMContext, db: Database, manager: UserbotManager) -> None:
    data = await state.get_data()
    user_id = message.from_user.id
    db_user = await db.get_user_by_tg_id(user_id)
    u_id = _get_user_id(db_user, user_id)
    
    svc = SubscriptionService(db)
    sub = await svc.get_subscription(u_id)
    if not sub or sub.plan == "free":
        b = InlineKeyboardBuilder()
        b.row(_btn(text="💎 Купить подписку", callback_data="sub:plans", style="success"))
        b.row(_btn(text="❌ Отмена", callback_data="menu:cancel", style="danger"))
        await message.answer(f"{e('❌')} <b>Доступ ограничен!</b>", reply_markup=b.as_markup(), parse_mode="HTML")
        return
        
    if not message.document.file_name.endswith('.txt'):
        await message.answer(f"{e('❌')} Пожалуйста, отправьте именно .txt файл.", reply_markup=cancel_kb(f"tools:parser:camp:{data.get('target_campaign_id')}" if data.get("target_campaign_id") else "tools:parser"))
        return
        
    import os
    import tempfile
    bot = message.bot
    file_id = message.document.file_id
    file_info = await bot.get_file(file_id)
    
    fd, path = tempfile.mkstemp()
    os.close(fd)
    
    try:
        await bot.download_file(file_info.file_path, destination=path)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        users_list = []
        for line in content.split('\n'):
            line = line.strip()
            if line:
                users_list.append(line)
                
    except Exception as exc:
        logger.error(f"Error reading users file: {exc}")
        await message.answer(f"{e('❌')} Ошибка при чтении файла.", reply_markup=cancel_kb(f"tools:parser:camp:{data.get('target_campaign_id')}" if data.get("target_campaign_id") else "tools:parser"))
        return
    finally:
        try: os.remove(path)
        except: pass
        
    if not users_list:
        await message.answer(f"{e('❌')} Файл пуст.", reply_markup=cancel_kb(f"tools:parser:camp:{data.get('target_campaign_id')}" if data.get("target_campaign_id") else "tools:parser"))
        return
        
    data = await state.get_data()
    account_id = data.get("parse_account_id")
    mode = data.get("parse_mode")
    
    status_msg = await message.answer(f"⏳ Подготовка к парсингу базы ({len(users_list)} строк)...")
    await state.clear()
    
    asyncio.create_task(manager.advanced_parse_users_list(account_id, users_list, mode, status_msg, message.from_user.id))

@router.callback_query(F.data.startswith("parser:download:"), StateFilter("*"))
async def cb_parser_download(callback: CallbackQuery, manager: UserbotManager) -> None:
    job_id = callback.data.split(":")[2]
    # We will fetch the partial results from the manager
    file_path = manager.get_parser_partial_result(job_id)
    if not file_path:
        await callback.answer("Результаты еще не готовы или процесс уже завершен.", show_alert=True)
        return
        
    try:
        doc = FSInputFile(file_path, filename=f"parsed_partial_{job_id}.txt")
        await callback.message.answer_document(doc, caption=f"{e('✅')} Ваша промежуточная база собрана!")
        await callback.answer()
    except Exception as exc:
        logger.error(f"Failed to send partial file: {exc}")
        await callback.answer("Ошибка при отправке файла.", show_alert=True)


@router.callback_query(F.data.startswith("tools:parser:camp:"), StateFilter("*"))
async def cb_tools_parser_camp(callback: CallbackQuery, state: FSMContext, db: Database, manager: UserbotManager) -> None:
    campaign_id = int(callback.data.split(":")[3])
    # The parser currently outputs a .txt file, which the user can then upload to the campaign.
    await cb_tools_parser(callback, state, db, manager, camp_id=campaign_id)
