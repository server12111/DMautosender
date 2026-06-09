"""
User registration & ban-check middleware.
Registers new users in the DB on first interaction.
Blocks banned users.
Injects 'bot_user' into handler data.
"""
from typing import Any, Callable, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from ..database.db import Database


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user

        if tg_user is None:
            return await handler(event, data)

        db: Database = data.get("db")
        if db is None:
            return await handler(event, data)

        username = tg_user.username
        full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
        bot_user = await db.get_or_create_user(tg_user.id, username, full_name)

        if bot_user.is_banned:
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Ваш аккаунт заблокирован. Обратитесь в поддержку."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Аккаунт заблокирован.", show_alert=True)
            return

        data["bot_user"] = bot_user
        return await handler(event, data)
