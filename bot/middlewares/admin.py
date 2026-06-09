"""
Admin-only middleware — used exclusively on admin routers.
"""
from typing import Any, Callable, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery


class AdminMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: list[int]) -> None:
        self._admin_ids = set(admin_ids)
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user is None or user.id not in self._admin_ids:
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Доступ запрещён. Только для администраторов."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Только для администраторов.", show_alert=True)
            return

        return await handler(event, data)
