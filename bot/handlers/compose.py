import logging
from ..utils.emoji import e
from aiogram import Router, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ..keyboards.inline import _btn
from aiogram import Bot
from aiogram.types import CallbackQuery, Message, PhotoSize
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..keyboards.inline import compose_menu_kb, back_to_menu_kb, cancel_kb, main_menu_kb
from ..database.db import Database
from ..database.models import Campaign

logger = logging.getLogger("dmsender.compose")

router = Router()

FORMAT_HELP = (
    f"{e('📝')} <b>Форматирование текста (HTML)</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "<b>жирный</b> → <code>&lt;b&gt;текст&lt;/b&gt;</code>\n"
    "<i>курсив</i> → <code>&lt;i&gt;текст&lt;/i&gt;</code>\n"
    "<u>подчёркнутый</u> → <code>&lt;u&gt;текст&lt;/u&gt;</code>\n"
    "<s>зачёркнутый</s> → <code>&lt;s&gt;текст&lt;/s&gt;</code>\n"
    "<code>моноширинный</code> → <code>&lt;code&gt;текст&lt;/code&gt;</code>\n"
    "<tg-spoiler>спойлер</tg-spoiler> → <code>&lt;tg-spoiler&gt;текст&lt;/tg-spoiler&gt;</code>\n"
    "<a href='https://t.me'>ссылка</a> → <code>&lt;a href='URL'&gt;текст&lt;/a&gt;</code>\n\n"
    f"<i>{e('💡')} Пример:</i>\n"
    "<code>Привет, &lt;b&gt;мир&lt;/b&gt;! Это &lt;i&gt;курсив&lt;/i&gt;</code>"
)

class ComposeStates(StatesGroup):
    waiting_text = State()
    waiting_image = State()
    waiting_file = State()

def _compose_menu_text(campaign: Campaign) -> str:
    text = campaign.text or ""
    image = e("✅") if campaign.image_file_id else e("❌")
    attach = campaign.attach_file_name or (e("✅") if campaign.attach_file_id else e("❌"))

    preview = (text[:200] + "...") if len(text) > 200 else text
    return (
        f"{e('📝')} <b>НАСТРОЙКА СООБЩЕНИЯ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e('📢')} Рассылка: <b>{campaign.name}</b>\n\n"
        f"{e('🖼')} <b>Изображение/Видео:</b> {image}\n"
        f"{e('📎')} <b>Файл:</b> {attach}\n\n"
        f"{e('📝')} <b>Текущий текст:</b>\n"
        f"{preview if preview else '<i>Текст пока не задан</i>'}"
    )

@router.callback_query(F.data.startswith("compose:menu:"), StateFilter("*"))
async def cb_compose_menu(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        return await callback.answer("Рассылка не найдена", show_alert=True)
        
    await callback.message.edit_text(
        _compose_menu_text(campaign),
        reply_markup=compose_menu_kb(
            campaign_id=campaign_id,
            has_image=bool(campaign.image_file_id),
            has_file=bool(campaign.attach_file_id),
        ),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("compose:set_text:"), StateFilter("*"))
async def cb_set_text(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    await state.set_state(ComposeStates.waiting_text)
    await callback.message.edit_text(
        "✏️ <b>Текст сообщения</b>\n\n"
        "Введите текст рассылки. Поддерживается HTML-форматирование:\n\n"
        "Отправьте текст сообщения:",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(StateFilter(ComposeStates.waiting_text))
async def fsm_receive_text(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()
        
    text = message.text or message.caption or ""
    if not text:
        await message.answer("❌ Сообщение пустое. Введите текст:", reply_markup=cancel_kb(f"compose:menu:{campaign_id}"))
        return

    await db.update_campaign_text(campaign_id, text)
    await state.clear()

    campaign = await db.get_campaign(campaign_id)
    await message.answer(
        "✅ Текст сохранён!\n\n" + _compose_menu_text(campaign),
        reply_markup=compose_menu_kb(
            campaign_id=campaign_id,
            has_image=bool(campaign.image_file_id),
            has_file=bool(campaign.attach_file_id),
        ),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("compose:set_image:"), StateFilter("*"))
async def cb_set_image(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    await state.set_state(ComposeStates.waiting_image)
    await callback.message.edit_text(
        "🖼 <b>Изображение</b>\n\n"
        "Отправьте фотографию (именно как фото, не как файл).\n"
        "Она будет прикреплена к каждому сообщению рассылки.",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(StateFilter(ComposeStates.waiting_image), F.photo)
async def fsm_receive_image(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()
        
    photo: PhotoSize = message.photo[-1]
    await db.update_campaign_attachments(campaign_id, image_file_id=photo.file_id)
    await state.clear()

    campaign = await db.get_campaign(campaign_id)
    await message.answer(
        "✅ Изображение сохранено!\n\n" + _compose_menu_text(campaign),
        reply_markup=compose_menu_kb(campaign_id=campaign_id, has_image=True, has_file=False),
        parse_mode="HTML",
    )

@router.message(StateFilter(ComposeStates.waiting_image), F.document)
async def fsm_image_as_doc(message: Message) -> None:
    await message.answer(
        "❌ Пожалуйста, отправьте фото <b>как фотографию</b>, а не как файл.",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"), parse_mode="HTML",
    )

@router.message(StateFilter(ComposeStates.waiting_image))
async def fsm_image_wrong(message: Message) -> None:
    await message.answer(
        "❌ Пожалуйста, отправьте <b>фотографию</b>.",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"), parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("compose:set_file:"), StateFilter("*"))
async def cb_set_file(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    await state.set_state(ComposeStates.waiting_file)
    await callback.message.edit_text(
        "📎 <b>Файл</b>\n\nОтправьте файл (документ):",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(StateFilter(ComposeStates.waiting_file), F.document)
async def fsm_receive_file(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return await state.clear()
        
    doc = message.document
    await db.update_campaign_attachments(campaign_id, attach_file_id=doc.file_id, attach_file_name=doc.file_name or "file")
    await state.clear()

    campaign = await db.get_campaign(campaign_id)
    await message.answer(
        f"✅ Файл «{doc.file_name}» сохранён!\n\n" + _compose_menu_text(campaign),
        reply_markup=compose_menu_kb(campaign_id=campaign_id, has_image=False, has_file=True),
        parse_mode="HTML",
    )

@router.message(StateFilter(ComposeStates.waiting_file))
async def fsm_file_wrong(message: Message) -> None:
    await message.answer(
        "❌ Пожалуйста, отправьте <b>файл</b> (документ).",
        reply_markup=cancel_kb(f"compose:menu:{campaign_id}"), parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("compose:clear_attach:"), StateFilter("*"))
async def cb_clear_attach(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await db.update_campaign_attachments(campaign_id, image_file_id=None, attach_file_id=None, attach_file_name=None)
    campaign = await db.get_campaign(campaign_id)
    await callback.message.edit_text(
        "✅ Вложения удалены.\n\n" + _compose_menu_text(campaign),
        reply_markup=compose_menu_kb(campaign_id=campaign_id, has_image=False, has_file=False),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("compose:preview:"), StateFilter("*"))
async def cb_preview(callback: CallbackQuery, db: Database, bot: Bot) -> None:
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    
    text = campaign.text or ""
    image_id = campaign.image_file_id or ""
    file_id = campaign.attach_file_id or ""

    if not text and not image_id and not file_id:
        await callback.answer("⚠️ Нет ни текста, ни вложения!", show_alert=True)
        return

    chat_id = callback.from_user.id
    try:
        if file_id:
            await bot.send_document(
                chat_id, file_id,
                caption=text or None,
                parse_mode="HTML",
            )
        elif image_id:
            await bot.send_photo(
                chat_id, image_id,
                caption=text or None,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        await callback.answer("✅ Предпросмотр отправлен!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("compose:format_help:"), StateFilter("*"))
async def cb_format_help(callback: CallbackQuery, db: Database) -> None:
    campaign_id = int(callback.data.split(":")[2])
    campaign = await db.get_campaign(campaign_id)
    await callback.message.edit_text(
        FORMAT_HELP,
        reply_markup=compose_menu_kb(
            campaign_id=campaign_id,
            has_image=bool(campaign.image_file_id),
            has_file=bool(campaign.attach_file_id),
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("compose:edit_text:"), StateFilter("*"))
async def cb_compose_edit_text(callback: CallbackQuery, state: FSMContext) -> None:
    campaign_id = int(callback.data.split(":")[2])
    await state.update_data(campaign_id=campaign_id)
    await state.set_state(ComposeStates.waiting_text)
    
    b = InlineKeyboardBuilder()
    b.row(_btn(text="Отмена", callback_data=f"compose:menu:{campaign_id}"))
    
    await callback.message.edit_text(
        f"{e('✏️')} <b>Изменение текста</b>\n\n" \
        f"Отправьте новый текст рассылки ответным сообщением.",
        reply_markup=b.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()
