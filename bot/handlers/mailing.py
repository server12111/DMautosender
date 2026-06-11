import asyncio
import logging
from aiogram.filters import StateFilter
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from ..keyboards.inline import mailing_status_kb, back_to_menu_kb, campaign_menu_kb
from ..database.db import Database
from ..database.models import BotUser, Campaign
from ..userbot.manager import UserbotManager
from ..services.sender import MailingSender, SendConfig

logger = logging.getLogger("dmsender.mailing")

router = Router()

# Per-campaign sender state: {campaign_id -> MailingSender}
_senders: dict[int, MailingSender] = {}
# Per-campaign status message info: {campaign_id -> (chat_id, msg_id)}
_status_info: dict[int, tuple[int, int]] = {}
# Per-campaign update tasks
_update_tasks: dict[int, asyncio.Task] = {}


def get_sender(campaign_id: int) -> MailingSender | None:
    return _senders.get(campaign_id)


def _build_send_config(campaign: Campaign) -> SendConfig:
    return SendConfig(
        message_text=campaign.text or "",
        parse_mode="html",
        image_file_id=campaign.image_file_id or None,
        attach_file_id=campaign.attach_file_id or None,
        attach_file_name=campaign.attach_file_name or None,
        delay_mode=campaign.delay_mode,
        delay_fixed=campaign.delay_fixed,
        delay_min=campaign.delay_min,
        delay_max=campaign.delay_max,
        pause_between_cycles=campaign.pause_cycles,
    )


async def _periodic_status_update(bot: Bot, db: Database, campaign_id: int) -> None:
    """Updates status message every 30 seconds for a specific campaign."""
    while True:
        sender = _senders.get(campaign_id)
        if not sender or not sender.is_running:
            break
        await asyncio.sleep(30)
        sender = _senders.get(campaign_id)
        if not sender or not sender.is_running:
            break
        try:
            stats = sender.stats
            db_stats = await db.get_targets_stats(campaign_id)
            text = _build_status_text(stats, db_stats)
            info = _status_info.get(campaign_id)
            if info:
                chat_id, msg_id = info
                await bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=mailing_status_kb(campaign_id, is_running=True),
                    parse_mode="HTML",
                )
        except Exception:
            pass


def _build_status_text(stats, db_stats: dict) -> str:
    return (
        f"▶️ <b>Рассылка идёт...</b>\n\n"
        f"├ Отправлено:    <b>{stats.sent}</b>\n"
        f"├ Ошибок:        <b>{stats.errors}</b>\n"
        f"├ Заблокировано: <b>{stats.blocked}</b>\n"
        f"├ Осталось:      <b>{db_stats.get('remaining', '?')}</b>\n"
        f"└ Скорость:      <b>{stats.speed_per_min()} msg/мин</b>"
    )


@router.callback_query(F.data.startswith("mailing:start:"), StateFilter("*"))
async def cb_mailing_start(
    callback: CallbackQuery, db: Database, manager: UserbotManager, bot: Bot,
) -> None:
    campaign_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    existing = _senders.get(campaign_id)
    if existing and existing.is_running:
        await callback.answer("⚠️ Рассылка уже запущена!", show_alert=True)
        return

    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return await callback.answer("Рассылка не найдена", show_alert=True)

    # Validation
    errors = []
    
    assigned_account_ids = await db.get_campaign_accounts(campaign_id)
    active_account_ids = [acc_id for acc_id in assigned_account_ids if manager.is_connected(acc_id)]
    
    if not active_account_ids:
        errors.append("— Нет подключенных аккаунтов, привязанных к этой рассылке.")

    db_stats = await db.get_targets_stats(campaign_id)
    if db_stats["remaining"] == 0:
        errors.append("— База получателей пуста или все уже обработаны.")

    if not campaign.text and not campaign.image_file_id and not campaign.attach_file_id:
        errors.append("— Не задан текст или вложения для рассылки.")

    if errors:
        msg = "<b>Не удалось запустить рассылку:</b>\n" + "\n".join(errors)
        try:
            await callback.message.edit_text(msg, reply_markup=campaign_menu_kb(campaign), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await callback.answer()
        return

    # Configuration and Start
    config = _build_send_config(campaign)

    sender = MailingSender(db, manager, bot, campaign_id, active_account_ids, config)
    _senders[campaign_id] = sender

    await db.update_campaign_status(campaign_id, "running")

    # Start sending process in background
    asyncio.create_task(sender.start())

    # Initial status message
    sender_stats = sender.stats
    text = _build_status_text(sender_stats, db_stats)
    msg = await callback.message.edit_text(
        text,
        reply_markup=mailing_status_kb(campaign_id, is_running=True),
        parse_mode="HTML",
    )
    _status_info[campaign_id] = (tg_id, msg.message_id)

    # Start periodic UI updater
    task = asyncio.create_task(_periodic_status_update(bot, db, campaign_id))
    _update_tasks[campaign_id] = task

    await callback.answer("✅ Рассылка запущена!")


@router.callback_query(F.data.startswith("mailing:stop:"), StateFilter("*"))
async def cb_mailing_stop(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    sender = _senders.get(campaign_id)
    
    await db.update_campaign_status(campaign_id, "stopped")

    if sender and sender.is_running:
        sender.stop()
        await callback.answer("🛑 Остановка...")

        if campaign_id in _update_tasks:
            _update_tasks[campaign_id].cancel()
            del _update_tasks[campaign_id]

        stats = sender.stats
        db_stats = await db.get_targets_stats(campaign_id)
        
        campaign = await db.get_campaign(campaign_id)

        text = (
            f"🛑 <b>Рассылка остановлена</b>\n\n"
            f"├ Отправлено:    <b>{stats.sent}</b>\n"
            f"├ Ошибок:        <b>{stats.errors}</b>\n"
            f"└ Заблокировано: <b>{stats.blocked}</b>\n\n"
            f"Всего осталось:  <b>{db_stats.get('remaining', 0)}</b>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=campaign_menu_kb(campaign),
            parse_mode="HTML",
        )
    else:
        campaign = await db.get_campaign(campaign_id)
        await callback.message.edit_text(
            "⚠️ Рассылка не была запущена.",
            reply_markup=campaign_menu_kb(campaign)
        )
        await callback.answer()
