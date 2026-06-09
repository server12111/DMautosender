"""Admin broadcast — send message to all bot users."""
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...keyboards.inline import admin_broadcast_kb, admin_back_kb, cancel_kb
from ...database.db import Database
from ...utils.emoji import e

logger = logging.getLogger("dmsender.admin.broadcast")
router = Router()


class BroadcastStates(StatesGroup):
    waiting_message = State()
    confirm = State()


@router.callback_query(F.data == "admin:broadcast:prepare", StateFilter("*"))
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_message)
    await callback.message.edit_text(
        f"{e('📢')} <b>Рассылка всем пользователям</b>\n\n"
        f"Отправьте сообщение, которое получат все пользователи бота.\n"
        f"Поддерживается: текст, фото, документ.\n\n"
        f"<b>⚠️ Действие необратимо!</b>",
        reply_markup=cancel_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(BroadcastStates.waiting_message))
async def fsm_broadcast_message(message: Message, state: FSMContext) -> None:
    # Store the message content for confirmation
    msg_data = {
        "text": message.text or message.caption or "",
        "photo_id": message.photo[-1].file_id if message.photo else None,
        "document_id": message.document.file_id if message.document else None,
        "doc_name": message.document.file_name if message.document else None,
    }
    await state.update_data(msg_data=msg_data)
    await state.set_state(BroadcastStates.confirm)

    preview = (msg_data["text"][:200] + "...") if len(msg_data["text"]) > 200 else msg_data["text"]
    media_str = ""
    if msg_data["photo_id"]:
        media_str = "\n📷 [фото прикреплено]"
    elif msg_data["document_id"]:
        media_str = f"\n📎 [{msg_data['doc_name']}]"

    await message.answer(
        f"{e('📢')} <b>Подтвердите рассылку</b>\n\n"
        f"<b>Предпросмотр сообщения:</b>\n"
        f"<blockquote>{preview or '(пусто)'}</blockquote>{media_str}\n\n"
        f"Рассылка уйдёт ВСЕМ пользователям бота. Подтвердить?",
        reply_markup=admin_broadcast_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:broadcast:start", StateFilter(BroadcastStates.confirm))
async def cb_broadcast_confirm(
    callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot
) -> None:
    data = await state.get_data()
    msg_data = data.get("msg_data", {})
    await state.clear()

    tg_ids = await db.get_all_tg_ids()
    total = len(tg_ids)

    status_msg = await callback.message.edit_text(
        f"{e('📡')} <b>Рассылка запущена...</b>\n\n"
        f"Получателей: <b>{total}</b>\nОтправлено: 0",
        parse_mode="HTML",
    )
    await callback.answer("📢 Запускаем рассылку...")

    sent = 0
    failed = 0

    for tg_id in tg_ids:
        try:
            if msg_data.get("photo_id"):
                await bot.send_photo(
                    tg_id,
                    msg_data["photo_id"],
                    caption=msg_data["text"] or None,
                    parse_mode="HTML",
                )
            elif msg_data.get("document_id"):
                await bot.send_document(
                    tg_id,
                    msg_data["document_id"],
                    caption=msg_data["text"] or None,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(tg_id, msg_data["text"], parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

        # Update status every 20 messages
        if (sent + failed) % 20 == 0:
            try:
                await status_msg.edit_text(
                    f"{e('📡')} <b>Рассылка...</b>\n\n"
                    f"Получателей: <b>{total}</b>\n"
                    f"Отправлено: <b>{sent}</b>\n"
                    f"Ошибок: <b>{failed}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        await asyncio.sleep(0.05)  # Anti-flood

    await status_msg.edit_text(
        f"{e('✅')} <b>Рассылка завершена!</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b>",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
