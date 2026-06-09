import time
from aiogram.filters import StateFilter
from aiogram import Router, F
from aiogram.types import CallbackQuery

from ..keyboards.inline import stats_kb, mailing_status_kb
from ..database.db import Database
from ..database.models import BotUser

router = Router()

def _format_stats(db_stats: dict, sender_stats=None, campaign_name: str = "") -> str:
    lines = [
        f"📊 <b>Статистика рассылки:</b> {campaign_name}\n",
        f"├ Всего в базе:   <b>{db_stats.get('total', 0)}</b>",
        f"├ Отправлено:     <b>{db_stats.get('sent', 0)}</b>",
        f"├ Ошибок:         <b>{db_stats.get('errors', 0)}</b>",
        f"├ Заблокировано:  <b>{db_stats.get('blocked', 0)}</b>",
        f"└ Осталось:       <b>{db_stats.get('remaining', 0)}</b>",
    ]
    if sender_stats and sender_stats.sent > 0:
        lines.append(f"\n⚡ Скорость: <b>{sender_stats.speed_per_min()} msg/мин</b>")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("stats:show:"), StateFilter("*"))
async def cb_stats(callback: CallbackQuery, db: Database) -> None:
    from .mailing import get_sender
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return await callback.answer("Рассылка не найдена", show_alert=True)
        
    db_stats = await db.get_targets_stats(campaign_id)
    sender = get_sender(campaign_id)
    sender_stats = sender.stats if sender else None

    is_running = sender is not None and sender.is_running

    try:
        await callback.message.edit_text(
            _format_stats(db_stats, sender_stats, campaign.name),
            reply_markup=stats_kb(campaign_id) if not is_running else mailing_status_kb(campaign_id, is_running=True),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()
