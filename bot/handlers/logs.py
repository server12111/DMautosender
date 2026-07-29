import html
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, FSInputFile

from ..database.db import Database
from ..keyboards.inline import logs_kb

logger = logging.getLogger("dmsender.logs")
router = Router()

LINES_PER_PAGE = 30
EXPORT_LIMIT = 10_000


@router.callback_query(F.data.startswith("logs:show:"), StateFilter("*"))
async def cb_logs_show(callback: CallbackQuery, db: Database) -> None:
    parts = callback.data.split(":")
    campaign_id = int(parts[2])
    requested_page = max(0, int(parts[3]))

    _, total = await db.get_send_log_page(campaign_id, 1)
    total_pages = max(1, (total + LINES_PER_PAGE - 1) // LINES_PER_PAGE)
    page = min(requested_page, total_pages - 1)
    entries, _ = await db.get_send_log_page(
        campaign_id, LINES_PER_PAGE, page * LINES_PER_PAGE
    )

    if not entries:
        text = "📋 <b>Логи рассылки</b>\n\n<i>Записей пока нет.</i>"
    else:
        lines = []
        for entry in entries:
            timestamp = (entry.sent_at or "—")[:19]
            identifier = entry.identifier
            if len(identifier) > 80:
                identifier = identifier[:79] + "…"
            error_text = entry.error_msg or ""
            if len(error_text) > 120:
                error_text = error_text[:119] + "…"
            error = (
                f" — {html.escape(error_text)}"
                if error_text
                else ""
            )
            lines.append(
                f"<code>{timestamp}</code> · "
                f"<b>{html.escape(entry.status)}</b> · "
                f"{html.escape(identifier)}{error}"
            )
        text = (
            f"📋 <b>Логи рассылки</b> "
            f"(стр. {page + 1}/{total_pages})\n\n"
            + "\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=logs_kb(campaign_id, page, total_pages),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("logs:download:"), StateFilter("*"))
async def cb_logs_download(
    callback: CallbackQuery, bot: Bot, db: Database
) -> None:
    campaign_id = int(callback.data.split(":")[2])
    entries, total = await db.get_send_log_page(campaign_id, EXPORT_LIMIT)
    if not entries:
        await callback.answer("⚠️ Записей пока нет.", show_alert=True)
        return

    fd, log_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as log_file:
            for entry in entries:
                log_file.write(
                    f"{entry.sent_at or '—'}\t{entry.status}\t"
                    f"{entry.identifier}\t{entry.error_msg or ''}\n"
                )
            if total > len(entries):
                log_file.write(
                    f"\nЭкспорт ограничен последними {len(entries)} "
                    f"записями из {total}.\n"
                )
        await bot.send_document(
            callback.from_user.id,
            FSInputFile(
                log_path,
                filename=f"campaign_{campaign_id}_log.txt",
            ),
            caption=f"📋 Лог рассылки #{campaign_id}",
        )
        await callback.answer("✅ Файл отправлен!")
    except Exception as exc:
        logger.exception(
            "Не удалось экспортировать лог рассылки %s", campaign_id
        )
        await callback.answer(f"❌ Ошибка: {exc}", show_alert=True)
    finally:
        try:
            os.remove(log_path)
        except OSError:
            pass


@router.callback_query(F.data == "noop", StateFilter("*"))
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
