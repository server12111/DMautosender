"""
User start handler — entry point for all users.
Handles /start, welcome screen, legal agreement, main menu.
"""
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from ..keyboards.inline import main_menu_kb, welcome_agree_kb, after_legal_kb
from ..database.db import Database
from ..database.models import BotUser
from ..utils.emoji import e
from ..config import config

logger = logging.getLogger("dmsender.start")
router = Router()

PRIVACY_TEXT = """
📄 <b>Политика конфиденциальности DMautosender</b>

<b>Последнее обновление: 01.06.2025</b>

<b>1. Сбор данных</b>
При использовании бота мы собираем:
• Telegram ID, имя пользователя и отображаемое имя
• Историю подписок и платежей
• Настройки рассылки (тексты, задержки)

<b>2. Цели обработки данных</b>
Данные используются исключительно для:
• Предоставления услуг рассылки
• Управления подписками и оплатой
• Технической поддержки пользователей

<b>3. Хранение данных</b>
Данные хранятся на защищённых серверах и не передаются третьим лицам, за исключением платёжных провайдеров (Platega, CryptoBot) при проведении оплаты.

<b>4. Telegram-аккаунты</b>
Сессии Telegram-аккаунтов хранятся в зашифрованном виде. Вы несёте ответственность за соблюдение правил Telegram при использовании рассылки.

<b>5. Удаление данных</b>
Вы можете запросить удаление своих данных через поддержку.

<b>6. Контакт</b>
По вопросам: обратитесь через кнопку «Поддержка» в профиле.
""".strip()

TERMS_TEXT = """
📋 <b>Пользовательское соглашение DMautosender</b>

<b>Последнее обновление: 01.06.2025</b>

<b>1. Предмет соглашения</b>
DMautosender предоставляет инструмент для автоматической отправки сообщений в Telegram через пользовательские аккаунты.

<b>2. Ответственность пользователя</b>
Вы несёте полную ответственность за:
• Содержание отправляемых сообщений
• Соблюдение правил Telegram (Terms of Service)
• Соблюдение законодательства вашей страны

<b>3. Запрещено</b>
• Рассылка спама, мошеннических сообщений
• Незаконная реклама и продвижение
• Нарушение прав третьих лиц
• Отправка 18+ материалов без согласия

<b>4. Подписка и оплата</b>
• Подписка активируется после успешной оплаты
• Возврат средств возможен в течение 24 часов при технической невозможности использования сервиса
• При блокировке аккаунта за нарушение правил возврат не производится

<b>5. Ограничение ответственности</b>
Мы не несём ответственности за блокировку Telegram-аккаунтов пользователей при нарушении правил Telegram.

<b>6. Изменения соглашения</b>
Продолжение использования бота после обновления соглашения означает согласие с новыми условиями.
""".strip()


def _welcome_text(user: BotUser) -> str:
    name = user.full_name or user.username or "пользователь"
    return (
        f"{e('👋')} Привет, <b>{name}</b>!\n\n"
        f"{e('🤖')} <b>DMautosender</b> — мощный инструмент для автоматической\n"
        f"рассылки сообщений через Telegram-аккаунты.\n\n"
        f"{e('🚀')} <b>Что умеет бот:</b>\n"
        f"• Управление несколькими Telegram-аккаунтами\n"
        f"• Загрузка баз получателей (username / ID)\n"
        f"• Рассылка с текстом, фото, файлами\n"
        f"• Гибкие задержки между сообщениями\n"
        f"• Детальная статистика и логи\n\n"
        f"{e('💳')} <b>Планы:</b> Free → Pro → Business\n\n"
        f"Для продолжения ознакомьтесь с документами и нажмите <b>«Принимаю»</b>."
    )


def _main_menu_text(user: BotUser) -> str:
    return (
        f"{e('👋')} Добро пожаловать в <b>DMautosender</b>!\n\n"
        f"{e('🟢')} <i>Ваш надежный помощник для автоматизации Telegram</i>\n\n"
        f"{e('➕')} <b>Начните работу:</b> создавайте новые рассылки, управляйте аккаунтами и отслеживайте статистику в пару кликов.\n\n"
        f"👇 <b>Выберите нужный раздел в меню ниже:</b>"
    )


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    
    referrer_id = None
    args = message.text.split(" ")
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].split("_")[1])
        except ValueError:
            pass

    user = await db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
        referrer_id=referrer_id
    )
    if not user.agreed_at:
        await message.answer(
            _welcome_text(user),
            reply_markup=welcome_agree_kb(),
            parse_mode="HTML",
        )
    else:
        is_admin = message.from_user.id in config.ADMIN_IDS
        plan = await db.get_subscription_plan(user.id)
        await message.answer(
            _main_menu_text(user),
            reply_markup=main_menu_kb(is_admin=is_admin, plan=plan),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "menu:welcome", StateFilter("*"))
async def cb_welcome(callback: CallbackQuery, db: Database) -> None:
    user = await db.get_user_by_tg_id(callback.from_user.id)
    if user:
        await callback.message.edit_text(
            _welcome_text(user),
            reply_markup=welcome_agree_kb(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "legal:privacy", StateFilter("*"))
async def cb_privacy(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        PRIVACY_TEXT, reply_markup=after_legal_kb(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "legal:terms", StateFilter("*"))
async def cb_terms(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        TERMS_TEXT, reply_markup=after_legal_kb(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "legal:agree", StateFilter("*"))
async def cb_agree(callback: CallbackQuery, db: Database) -> None:
    await db.set_user_agreed(callback.from_user.id)
    is_admin = callback.from_user.id in config.ADMIN_IDS
    user = await db.get_user_by_tg_id(callback.from_user.id)
    await callback.message.edit_text(
        f"{e('✅')} <b>Соглашение принято!</b>\n\n"
        f"{e('🎁')} Вам активирован <b>бесплатный пробный период (3 дня Free)</b>.\n\n"
        f"Для полного доступа оформите подписку через раздел <b>Профиль → Подписка</b>.\n\n"
        + _main_menu_text(user),
        reply_markup=main_menu_kb(is_admin=is_admin),
        parse_mode="HTML",
    )
    await callback.answer("✅ Добро пожаловать!")


@router.callback_query(F.data == "menu:main", StateFilter("*"))
async def cb_main_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    is_admin = callback.from_user.id in config.ADMIN_IDS
    user = await db.get_user_by_tg_id(callback.from_user.id)
    plan = await db.get_subscription_plan(user.id) if user else "free"
    try:
        await callback.message.edit_text(
            _main_menu_text(user),
            reply_markup=main_menu_kb(is_admin=is_admin, plan=plan),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            _main_menu_text(user),
            reply_markup=main_menu_kb(is_admin=is_admin, plan=plan),
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(StateFilter(None))
async def fallback_handler(message: Message, db: Database) -> None:
    user = await db.get_user_by_tg_id(message.from_user.id)
    is_admin = message.from_user.id in config.ADMIN_IDS
    if user and user.agreed_at:
        plan = await db.get_subscription_plan(user.id)
        await message.answer(
            "Нажмите /start или выберите раздел:",
            reply_markup=main_menu_kb(is_admin=is_admin, plan=plan),
        )
    else:
        await message.answer("Нажмите /start чтобы начать.")

@router.callback_query(F.data == "sender:menu", StateFilter("*"))
async def cb_sender_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    from ..services.sender import SenderService
    from ..keyboards.inline import sender_menu_kb
    sender_service = callback.bot.get("sender_service")
    is_running = False
    if sender_service:
        is_running = sender_service.is_running(callback.from_user.id)
    await callback.message.edit_text(
        "🚀 <b>Управление Рассылкой</b>\n\nЗдесь вы можете загрузить базу, настроить текст, задержки и управлять процессом.",
        reply_markup=sender_menu_kb(is_running=is_running),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu:cancel", StateFilter("*"))
async def cb_menu_cancel(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await cb_main_menu(callback, state, db)

