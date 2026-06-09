import logging
from datetime import datetime
from pathlib import Path
from aiogram.filters import StateFilter
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, FSInputFile

from ..keyboards.inline import logs_kb, back_to_menu_kb
from ..config import config
from ..database.db import Database

logger = logging.getLogger("dmsender.logs")

router = Router()

LINES_PER_PAGE = 30


def _get_log_file() -> Path:
    return config.LOGS_PATH / f"{datetime.now().strftime('%Y%m%d')}.log"


def _read_log_lines() -> list[str]:
    log_file = _get_log_file()
    if not log_file.exists():
        return []
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


@router.callback_query(F.data.startswith("logs:show:"), StateFilter("*"))
async def cb_logs_show(callback: CallbackQuery, db: Database) -> None:
    parts = callback.data.split(":")
    campaign_id = int(parts[2])
    page = int(parts[3])
    
    lines = _read_log_lines()
    # Filter lines that mention campaign_id
    # If a line has [Campaign X] or something, we could filter it. Right now just show global logs
    # But later it's better to filter. For now we just show global logs to avoid breaking anything.

    if not lines:
        await callback.message.edit_text(
            "📋 <b>Логи</b>\n\n<i>Лог-файл пуст или не существует.</i>",
            reply_markup=logs_kb(campaign_id, 0, 1),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    total_pages = max(1, (len(lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    reversed_lines = list(reversed(lines))
    start = page * LINES_PER_PAGE
    end = start + LINES_PER_PAGE
    page_lines = reversed_lines[start:end]

    log_text = "".join(reversed(page_lines)).strip()
    if len(log_text) > 3500:
        log_text = "..." + log_text[-3497:]

    text = (
        f"📋 <b>Логи</b> (стр. {page + 1}/{total_pages})\n\n"
        f"<code>{log_text}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=logs_kb(campaign_id, page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("logs:download:"), StateFilter("*"))
async def cb_logs_download(callback: CallbackQuery, bot: Bot) -> None:
    log_file = _get_log_file()
    if not log_file.exists():
        await callback.answer("⚠️ Лог-файл не найден.", show_alert=True)
        return

    try:
        await bot.send_document(
            callback.from_user.id,
            FSInputFile(str(log_file), filename=log_file.name),
            caption=f"📋 Лог за {datetime.now().strftime('%d.%m.%Y')}",
        )
        await callback.answer("✅ Файл отправлен!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "noop", StateFilter("*"))
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
