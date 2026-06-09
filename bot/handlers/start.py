from aiogram import Router, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from ..keyboards.inline import main_menu_kb

router = Router()

MENU_TEXT = (
    "🤖 <b>DMautosender</b> — рассылка через пользовательские аккаунты Telegram\n\n"
    "Выберите действие:"
)


# Работает в ЛЮБОМ состоянии FSM — сбрасывает всё и показывает меню
@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:main", StateFilter("*"))
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(MENU_TEXT, reply_markup=main_menu_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(MENU_TEXT, reply_markup=main_menu_kb(), parse_mode="HTML")
    await callback.answer()


# Fallback: любое необработанное сообщение вне стейта
@router.message(StateFilter(None))
async def fallback_handler(message: Message) -> None:
    await message.answer(
        "Нажмите /start чтобы открыть меню.",
        reply_markup=main_menu_kb(),
    )
