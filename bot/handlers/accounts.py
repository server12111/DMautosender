import html
import logging
import os
import shutil
import tempfile
import zipfile
import uuid
from ..utils.emoji import e
from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    PhoneNumberInvalidError,
    ApiIdInvalidError,
)

from ..keyboards.inline import (
    accounts_list_kb, account_actions_kb, account_delete_confirm_kb,
    cancel_kb, back_to_menu_kb, main_menu_kb, skip_proxy_kb, code_pad_kb, api_id_kb, api_hash_kb,
)
from ..database.db import Database
from ..database.models import BotUser
from ..userbot.manager import UserbotManager
from ..services.subscription import SubscriptionService
from ..config import config

logger = logging.getLogger("dmsender.accounts")

router = Router()

MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def _safe_extract_zip(zip_ref: zipfile.ZipFile, destination: str) -> None:
    """Extract an archive without allowing paths outside ``destination``."""
    base = os.path.realpath(destination)
    members = zip_ref.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError("Слишком много файлов в архиве")
    if sum(member.file_size for member in members) > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError("Архив слишком большой после распаковки")
    for member in members:
        target = os.path.realpath(os.path.join(base, member.filename))
        if target != base and not target.startswith(base + os.sep):
            raise ValueError("Архив содержит небезопасный путь")
    zip_ref.extractall(base)


async def _install_session_file(
    source: str,
    destination: str,
    existing_account_id: int | None,
    manager: UserbotManager,
) -> None:
    """Atomically replace a session after its live client is disconnected."""
    staging = f"{destination}.tmp_{uuid.uuid4().hex}"
    try:
        shutil.copy2(source, staging)
        if existing_account_id is not None:
            await manager.disconnect_account(existing_account_id)
        os.replace(staging, destination)
    finally:
        try:
            os.remove(staging)
        except FileNotFoundError:
            pass


class AddAccountStates(StatesGroup):
    api_id = State()
    api_hash = State()
    proxy = State()
    phone = State()
    code = State()
    password = State()


def _get_user_id(db_user: BotUser | None, tg_id: int) -> int:
    """Returns internal DB user.id (not Telegram ID)."""
    return db_user.id if db_user else 0


# ── Список аккаунтов ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "accounts:list", StateFilter("*"))
async def cb_accounts_list(
    callback: CallbackQuery, db: Database, manager: UserbotManager,
    state: FSMContext,
    bot_user: BotUser = None,
) -> None:
    await state.clear()
    user_id = _get_user_id(bot_user, callback.from_user.id)
    accounts = await db.get_all_accounts(user_id)
    for acc in accounts:
        if manager.is_connected(acc.id):
            acc.status = "connected"

    # Show plan limits hint
    svc = SubscriptionService(db)
    plan = await svc.get_plan(user_id)
    from ..database.models import PLAN_LIMITS
    info = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_acc = info["max_accounts"]
    acc_str = "∞" if max_acc == -1 else str(max_acc)

    if not accounts:
        text = (
            f"{e('📱')} <b>ВАШИ АККАУНТЫ</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"У вас пока нет добавленных аккаунтов.\n"
            f"<i>Нажмите «{e('➕')} Добавить аккаунт», чтобы подключить первый!</i>\n\n"
            f"Ваш лимит: <b>{acc_str}</b> аккаунт(ов) [{info['emoji']} {info['label']}]"
        )
    else:
        lines = [f"{e('📱')} <b>ВАШИ АККАУНТЫ</b> ({len(accounts)}/{acc_str})\n━━━━━━━━━━━━━━━━━━\n"]
        for acc in accounts:
            if manager.is_connected(acc.id):
                status = "🟢 подключён"
            elif acc.status == "banned":
                status = "⛔ заблокирован"
            else:
                status = "🔴 не подключён"
            lines.append(f" ├ <b>{acc.phone}</b> — {status}")
        text = "\n".join(lines)

    await callback.message.edit_text(
        text, reply_markup=accounts_list_kb(accounts), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:view:"), StateFilter("*"))
async def cb_account_view(
    callback: CallbackQuery, db: Database, manager: UserbotManager
) -> None:
    account_id = int(callback.data.split(":")[2])
    acc = await db.get_account(account_id)
    if not acc:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    connected = manager.is_connected(account_id)
    status_icon = "🟢" if connected else "🔴"
    status_text = "подключён" if connected else acc.status

    text = (
        f"{e('📱')} <b>УПРАВЛЕНИЕ АККАУНТОМ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('📞')} <b>Телефон:</b> <code>{acc.phone}</code>\n"
        f"{e('👤')} <b>Имя:</b> {html.escape(acc.name or '—')}\n"
        f"{e('🔑')} <b>API ID:</b> <code>{acc.api_id}</code>\n\n"
        f"{e('🔄')} <b>Статус:</b> {status_icon} <b>{status_text}</b>\n"
    )
    await callback.message.edit_text(
        text, reply_markup=account_actions_kb(account_id), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:reconnect:"), StateFilter("*"))
async def cb_account_reconnect(
    callback: CallbackQuery, db: Database, manager: UserbotManager
) -> None:
    account_id = int(callback.data.split(":")[2])
    await callback.answer("🔄 Переподключаем...", show_alert=False)
    ok = await manager.reconnect(account_id)
    msg = "✅ Аккаунт успешно переподключён!" if ok else "❌ Не удалось подключиться."
    await callback.message.edit_text(msg, reply_markup=account_actions_kb(account_id))


@router.callback_query(F.data.startswith("accounts:delete:"), StateFilter("*"))
async def cb_account_delete_ask(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[2])
    await callback.message.edit_text(
        "🗑 <b>Удалить аккаунт?</b>\n\nЗапись в БД и локальный файл сессии будут удалены.",
        reply_markup=account_delete_confirm_kb(account_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_confirm:"), StateFilter("*"))
async def cb_account_delete_confirm(
    callback: CallbackQuery, db: Database, manager: UserbotManager
) -> None:
    account_id = int(callback.data.split(":")[2])
    await manager.remove_account(account_id)
    await callback.message.edit_text("✅ Аккаунт удалён.", reply_markup=back_to_menu_kb())
    await callback.answer()


# ── Добавление аккаунта (FSM) ─────────────────────────────────────────────────

@router.callback_query(F.data == "accounts:add", StateFilter("*"))
async def cb_accounts_add(
    callback: CallbackQuery, state: FSMContext, manager: UserbotManager,
    db: Database, bot_user: BotUser = None,
) -> None:
    user_id = _get_user_id(bot_user, callback.from_user.id)

    # Check plan limit
    svc = SubscriptionService(db)
    current_count = await db.count_user_accounts(user_id)
    if not await svc.can_add_account(user_id, current_count):
        plan = await svc.get_plan(user_id)
        from ..database.models import PLAN_LIMITS
        info = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        await callback.answer(
            f"❌ Лимит аккаунтов плана {info['label']} исчерпан ({info['max_accounts']} шт.).\n"
            f"Обновите подписку для увеличения лимита.",
            show_alert=True,
        )
        return

    await manager.cancel_authorization(callback.from_user.id)
    await state.clear()
    await state.set_state(AddAccountStates.api_id)
    text = (
        "👤 <b>Добавление аккаунта</b>\n\n"
        "Шаг 1/4. Введите <b>API ID</b>\n\n"
        "Получить: <a href='https://my.telegram.org'>my.telegram.org</a> → API development tools\n\n"
        "Или введите <code>0</code> чтобы использовать стандартный API."
    )
    await callback.message.edit_text(
        text, reply_markup=api_id_kb("accounts:list"), parse_mode="HTML", disable_web_page_preview=True
    )
    await callback.answer()


@router.message(StateFilter(AddAccountStates.api_id))
async def fsm_api_id(message: Message, state: FSMContext) -> None:
    text = message.text.strip() if message.text else ""
    if text == "0":
        await state.update_data(api_id=config.DEFAULT_API_ID, api_hash=config.DEFAULT_API_HASH)
        await state.set_state(AddAccountStates.proxy)
        await message.answer(
            "✅ Используем стандартный API.\n\n"
            "Шаг 2/5. Введите <b>Прокси</b> в формате <code>socks5://user:pass@ip:port</code>\n"
            "или нажмите «Пропустить», если прокси не нужен:",
            reply_markup=skip_proxy_kb(), parse_mode="HTML",
        )
        return

    if not text.isdigit():
        await message.answer("❌ API ID должен быть числом. Попробуйте ещё раз:", reply_markup=api_id_kb("accounts:list"))
        return

    api_id_int = int(text)
    if api_id_int > 2147483647 or api_id_int < 1:
        await message.answer(
            "❌ Неверный API ID. Это должно быть число от 1 до 2147483647.\n\n"
            "Вы случайно ввели номер телефона? API ID берётся с <a href='https://my.telegram.org'>my.telegram.org</a>",
            reply_markup=api_id_kb("accounts:list"), parse_mode="HTML", disable_web_page_preview=True
        )
        return

    await state.update_data(api_id=api_id_int)
    await state.set_state(AddAccountStates.api_hash)
    await message.answer("Шаг 2/5. Введите <b>API Hash</b>:", reply_markup=api_hash_kb(), parse_mode="HTML")


@router.message(StateFilter(AddAccountStates.api_hash))
async def fsm_api_hash(message: Message, state: FSMContext) -> None:
    text = message.text.strip() if message.text else ""
    if len(text) < 10:
        await message.answer("❌ API Hash слишком короткий. Попробуйте ещё раз:", reply_markup=cancel_kb("accounts:list"))
        return
    await state.update_data(api_hash=text)
    await state.set_state(AddAccountStates.proxy)
    await message.answer(
        "Шаг 3/5. Введите <b>Прокси</b> в формате <code>socks5://user:pass@ip:port</code>\n"
        "или нажмите «Пропустить», если прокси не нужен:",
        reply_markup=skip_proxy_kb(), parse_mode="HTML",
    )

@router.callback_query(F.data == "accounts:skip_proxy", StateFilter(AddAccountStates.proxy))
async def cb_skip_proxy(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(proxy=None)
    await state.set_state(AddAccountStates.phone)
    await callback.message.edit_text(
        "Шаг 4/5. Введите <b>номер телефона</b> (с + и кодом страны):\n"
        "Пример: <code>+79001234567</code>",
        reply_markup=cancel_kb("accounts:list"), parse_mode="HTML",
    )
    await callback.answer()

@router.message(StateFilter(AddAccountStates.proxy))
async def fsm_proxy(message: Message, state: FSMContext) -> None:
    proxy = (message.text or "").strip()
    from ..utils.proxy import parse_proxy
    if not parse_proxy(proxy):
        await message.answer(
            "❌ Неверный формат прокси. Пример: <code>socks5://user:pass@ip:port</code>\nПопробуйте ещё раз:",
            reply_markup=skip_proxy_kb(), parse_mode="HTML"
        )
        return
    await state.update_data(proxy=proxy)
    await state.set_state(AddAccountStates.phone)
    await message.answer(
        "✅ Прокси принят.\n\n"
        "Шаг 4/5. Введите <b>номер телефона</b> (с + и кодом страны):\n"
        "Пример: <code>+79001234567</code>",
        reply_markup=cancel_kb("accounts:list"), parse_mode="HTML",
    )


@router.message(StateFilter(AddAccountStates.phone))
async def fsm_phone(message: Message, state: FSMContext, manager: UserbotManager, db: Database) -> None:
    phone = message.text.strip() if message.text else ""
    if not phone.startswith("+") or len(phone) < 8:
        await message.answer(
            "❌ Неверный формат. Используйте формат <code>+79001234567</code>",
            reply_markup=cancel_kb("accounts:list"), parse_mode="HTML",
        )
        return

    data = await state.get_data()
    api_id: int = int(data["api_id"])
    api_hash: str = str(data["api_hash"])
    proxy: str | None = data.get("proxy")

    status_msg = await message.answer("📨 Отправляем код подтверждения...")

    try:
        db_user = await db.get_user_by_tg_id(message.from_user.id)
        u_id = _get_user_id(db_user, message.from_user.id)
        await manager.send_code(
            phone, api_id, api_hash, user_id=u_id, proxy=proxy,
            auth_key=message.from_user.id,
        )
    except PhoneNumberInvalidError:
        await status_msg.edit_text("❌ Неверный номер телефона.", reply_markup=cancel_kb("accounts:list"))
        return
    except ApiIdInvalidError:
        await status_msg.edit_text("❌ Неверный API ID или API Hash.", reply_markup=cancel_kb("accounts:list"))
        await state.clear()
        return
    except Exception as e:
        import html
        logger.error("send_code error: %s", e)
        await status_msg.edit_text(f"❌ Ошибка: {html.escape(str(e))}", reply_markup=cancel_kb("accounts:list"))
        return

    await state.update_data(phone=phone, api_id=api_id, api_hash=api_hash, current_code="")
    await state.set_state(AddAccountStates.code)
    await status_msg.edit_text(
        "✅ Код отправлен!\n\n"
        "Шаг 4/5. Введите <b>код</b> из SMS или Telegram:\n"
        "Введено: <code>_</code>",
        reply_markup=code_pad_kb(""), parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("code_pad:"), StateFilter("*"), StateFilter(AddAccountStates.code))
async def cb_code_pad(callback: CallbackQuery, state: FSMContext, manager: UserbotManager) -> None:
    action = callback.data.split(":")[1]
    data = await state.get_data()
    code = data.get("current_code", "")

    if action == "ignore":
        await callback.answer()
        return
    elif action == "del":
        code = code[:-1]
    elif action == "submit":
        if len(code) < 5:
            await callback.answer("❌ Код слишком короткий", show_alert=True)
            return
        
        # trigger sign in
        try:
            await manager.sign_in(code, callback.from_user.id)
        except SessionPasswordNeededError:
            await state.set_state(AddAccountStates.password)
            await callback.message.edit_text(
                "🔐 Шаг 5/5. На аккаунте включена <b>двухфакторная аутентификация</b>.\n\n"
                "Введите <b>пароль 2FA</b>:",
                reply_markup=cancel_kb("accounts:list"), parse_mode="HTML",
            )
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await callback.answer("❌ Неверный или устаревший код!", show_alert=True)
            return
        except Exception as e:
            logger.error("sign_in error: %s", e)
            await callback.message.edit_text(
                f"❌ Ошибка входа: {html.escape(str(e))}",
                reply_markup=cancel_kb("accounts:list"),
            )
            return

        await _finish_auth(callback.message, state, manager)
        return
    else:
        # It's a digit
        if len(code) < 5:
            code += action

    await state.update_data(current_code=code)
    display_code = code if code else "_"
    await callback.message.edit_text(
        "✅ Код отправлен!\n\n"
        "Шаг 4/5. Введите <b>код</b> из SMS или Telegram:\n"
        f"Введено: <code>{display_code}</code>",
        reply_markup=code_pad_kb(code), parse_mode="HTML",
    )
@router.message(StateFilter(AddAccountStates.code))
async def fsm_code(message: Message, state: FSMContext, manager: UserbotManager) -> None:
    code = (message.text or "").strip().replace(" ", "")
    if not code.isdigit():
        await message.answer("❌ Код должен состоять из цифр. Попробуйте ещё раз:", reply_markup=cancel_kb("accounts:list"))
        return

    try:
        await manager.sign_in(code, message.from_user.id)
    except SessionPasswordNeededError:
        await state.set_state(AddAccountStates.password)
        await message.answer(
            "🔐 Шаг 5/5. На аккаунте включена <b>двухфакторная аутентификация</b>.\n\n"
            "Введите <b>пароль 2FA</b>:",
            reply_markup=cancel_kb("accounts:list"), parse_mode="HTML",
        )
        return
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await message.answer("❌ Неверный или устаревший код. Попробуйте ещё раз:", reply_markup=cancel_kb("accounts:list"))
        return
    except Exception as e:
        logger.error("sign_in error: %s", e)
        await message.answer(
            f"❌ Ошибка входа: {html.escape(str(e))}",
            reply_markup=cancel_kb("accounts:list"),
        )
        return

    await _finish_auth(message, state, manager)


@router.message(StateFilter(AddAccountStates.password))
async def fsm_password(message: Message, state: FSMContext, manager: UserbotManager) -> None:
    password = message.text or ""
    try:
        await manager.sign_in_2fa(password, message.from_user.id)
    except PasswordHashInvalidError:
        await message.answer("❌ Неверный пароль 2FA. Попробуйте ещё раз:", reply_markup=cancel_kb("accounts:list"))
        return
    except Exception as e:
        logger.error("sign_in_2fa error: %s", e)
        await message.answer(
            f"❌ Ошибка 2FA: {html.escape(str(e))}",
            reply_markup=cancel_kb("accounts:list"),
        )
        return

    await _finish_auth(message, state, manager)


async def _finish_auth(
    message: Message, state: FSMContext, manager: UserbotManager
) -> None:
    data = await state.get_data()
    logger.info("_finish_auth: начинаем сохранение аккаунта, data keys=%s", list(data.keys()))
    await state.clear()

    try:
        account = await manager.finish_authorization(
            data["api_id"], data["api_hash"], message.from_user.id
        )
        logger.info("_finish_auth: аккаунт %s сохранён", account.phone)
        is_admin = message.from_user.id in config.ADMIN_IDS
        await message.answer(
            f"✅ <b>Аккаунт добавлен!</b>\n\n"
            f"{e('📱')} Телефон: <code>{account.phone}</code>\n"
            f"{e('👤')} Имя: {html.escape(account.name or '—')}\n\n"
            f"Теперь можно загружать базу и запускать рассылку.",
            reply_markup=main_menu_kb(is_admin=is_admin),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("_finish_auth ошибка: %s", exc, exc_info=True)
        await manager.cancel_authorization(message.from_user.id)
        is_admin = message.from_user.id in config.ADMIN_IDS
        await message.answer(
            f"❌ Ошибка сохранения аккаунта: {html.escape(str(exc))}\n\n"
            "Попробуйте добавить снова.",
            reply_markup=main_menu_kb(is_admin=is_admin),
        )

from ..config import config
from telethon import TelegramClient

try:
    from opentele.td import TDesktop
    from opentele.api import API, CreateNewSession
    _OPENTELE_AVAILABLE = True
except ImportError:
    _OPENTELE_AVAILABLE = False

class MassAddStates(StatesGroup):
    waiting_zip = State()

@router.callback_query(F.data == "accounts:mass_add", StateFilter("*"))
async def cb_mass_add(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if not user:
        return
    svc = SubscriptionService(db)
    current_count = await db.count_user_accounts(user.id)
    if not await svc.can_add_account(user.id, current_count):
        await callback.answer("❌ Лимит аккаунтов вашего тарифа исчерпан.", show_alert=True)
        return
        
    await state.set_state(MassAddStates.waiting_zip)
    await callback.message.edit_text(
        "📦 <b>Массовая загрузка аккаунтов</b>\n\n"
        "Отправьте ZIP-архив, содержащий папки <b>tdata</b> или файлы <b>.session</b>.\n\n"
        "<i>Бот автоматически найдет все аккаунты в архиве, сконвертирует их и добавит в вашу базу.</i>",
        reply_markup=cancel_kb("accounts:list"),
        parse_mode="HTML"
    )

@router.message(F.document, StateFilter(MassAddStates.waiting_zip))
async def mass_add_zip(message: Message, state: FSMContext, db: Database, manager: UserbotManager, bot: Bot) -> None:
    if not (message.document.file_name or "").lower().endswith('.zip'):
        await message.answer("❌ Пожалуйста, отправьте ZIP архив.", reply_markup=cancel_kb("accounts:list"))
        return
        
    user = await db.get_user_by_tg_id(message.from_user.id)
    if not user:
        return
    svc = SubscriptionService(db)
    max_accounts = await svc.get_limit(user.id, "max_accounts")
    current_count = await db.count_user_accounts(user.id)
    available_slots = (
        None if max_accounts == -1 else max(0, max_accounts - current_count)
    )
    if available_slots == 0:
        await state.clear()
        await message.answer(
            "❌ Лимит аккаунтов вашего тарифа исчерпан.",
            reply_markup=back_to_menu_kb(),
        )
        return
        
    status_msg = await message.answer("⏳ Скачиваю архив...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "accounts.zip")
        await bot.download(message.document, destination=zip_path)
        
        await status_msg.edit_text("⏳ Распаковываю и ищу tdata / .session...")
        
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                _safe_extract_zip(zip_ref, extract_dir)
        except Exception as e:
            await status_msg.edit_text(
                f"❌ Ошибка чтения архива: {html.escape(str(e))}"
            )
            return
            
        added_count = 0
        error_count = 0
        limit_reached = False
        
        for root, dirs, files in os.walk(extract_dir):
            if limit_reached or (
                available_slots is not None and added_count >= available_slots
            ):
                limit_reached = True
                break
            if "tdata" in dirs:
                tdata_path = os.path.join(root, "tdata")
                if not _OPENTELE_AVAILABLE:
                    error_count += 1
                else:
                    client = None
                    existing_account = None
                    try:
                        tdesk = TDesktop(tdata_path)
                        temp_session = os.path.join(
                            tmpdir, f"temp_{uuid.uuid4().hex}.session"
                        )
                        api = API.TelegramDesktop
                        client = await tdesk.ToTelethon(session=temp_session, flag=CreateNewSession, api=api)
                        await client.connect()
                        me = await client.get_me()
                        if me:
                            phone = f"+{me.phone}" if me.phone else f"id_{me.id}"
                            phone_clean = phone.replace("+", "")
                            final_session = os.path.join(
                                manager._sessions_path,
                                f"{user.id}_{phone_clean}.session",
                            )
                            await client.disconnect()
                            existing_account = await db.get_account_by_phone(
                                user.id, phone
                            )
                            await _install_session_file(
                                temp_session,
                                final_session,
                                existing_account.id if existing_account else None,
                                manager,
                            )
                            acc_id = await db.add_account(
                                user_id=user.id,
                                phone=phone,
                                api_id=config.DEFAULT_API_ID,
                                api_hash=config.DEFAULT_API_HASH,
                                name=me.first_name,
                                session_file=final_session,
                                max_accounts=max_accounts,
                            )
                            if acc_id is None:
                                if not existing_account:
                                    try:
                                        os.remove(final_session)
                                    except FileNotFoundError:
                                        pass
                                limit_reached = True
                                break
                            await manager.reconnect(acc_id)
                            added_count += 1
                        else:
                            error_count += 1
                            await client.disconnect()
                    except Exception:
                        error_count += 1
                        if (
                            existing_account
                            and not manager.is_connected(existing_account.id)
                        ):
                            await manager.reconnect(existing_account.id)
                    finally:
                        if client and client.is_connected():
                            await client.disconnect()
                dirs.remove("tdata")
                
            for file in files:
                if available_slots is not None and added_count >= available_slots:
                    limit_reached = True
                    break
                if file.lower().endswith(".session"):
                    session_path = os.path.join(root, file)
                    client = None
                    existing_account = None
                    try:
                        client = TelegramClient(
                            session_path,
                            config.DEFAULT_API_ID,
                            config.DEFAULT_API_HASH,
                        )
                        await client.connect()
                        me = await client.get_me()
                        if me:
                            phone = f"+{me.phone}" if me.phone else f"id_{me.id}"
                            phone_clean = phone.replace("+", "")
                            final_session = os.path.join(
                                manager._sessions_path,
                                f"{user.id}_{phone_clean}.session",
                            )
                            
                            await client.disconnect()
                            existing_account = await db.get_account_by_phone(
                                user.id, phone
                            )
                            await _install_session_file(
                                session_path,
                                final_session,
                                existing_account.id if existing_account else None,
                                manager,
                            )
                            
                            acc_id = await db.add_account(
                                user_id=user.id, 
                                phone=phone, 
                                api_id=config.DEFAULT_API_ID,
                                api_hash=config.DEFAULT_API_HASH,
                                name=me.first_name, 
                                session_file=final_session,
                                max_accounts=max_accounts,
                            )
                            if acc_id is None:
                                if not existing_account:
                                    try:
                                        os.remove(final_session)
                                    except FileNotFoundError:
                                        pass
                                limit_reached = True
                                break
                            await manager.reconnect(acc_id)
                            added_count += 1
                        else:
                            error_count += 1
                            await client.disconnect()
                    except Exception as e:
                        error_count += 1
                        if (
                            existing_account
                            and not manager.is_connected(existing_account.id)
                        ):
                            await manager.reconnect(existing_account.id)
                    finally:
                        if client and client.is_connected():
                            await client.disconnect()
                            
        await state.clear()
        await status_msg.edit_text(
            f"✅ <b>Массовая загрузка завершена</b>\n\n"
            f"Успешно добавлено: <b>{added_count}</b>\n"
            f"Ошибок: <b>{error_count}</b>"
            + (
                "\n⚠️ Остальные аккаунты пропущены из-за лимита тарифа."
                if limit_reached else ""
            ),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "add_acc:use_default_api", StateFilter(AddAccountStates.api_id))
async def cb_use_default_api(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(api_id=config.DEFAULT_API_ID)
    await state.set_state(AddAccountStates.api_hash)
    await callback.message.edit_text(
        "Выбран стандартный API ID.\n"
        "Теперь отправьте ваш <b>API Hash</b>:",
        reply_markup=api_hash_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "add_acc:use_default_api_hash", StateFilter(AddAccountStates.api_hash))
async def cb_use_default_api_hash(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(api_id=config.DEFAULT_API_ID, api_hash=config.DEFAULT_API_HASH)
    await state.set_state(AddAccountStates.phone)
    await callback.message.edit_text(
        "Выбран стандартный API Hash.\n"
        "Шаг 3/4. Отправьте <b>Номер телефона</b> в международном формате (например, +1234567890):",
        reply_markup=cancel_kb("accounts:list"),
        parse_mode="HTML"
    )
    await callback.answer()
