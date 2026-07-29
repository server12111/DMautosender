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


def _callback_resource(callback_data: str) -> tuple[str, int] | None:
    """Return (resource type, id) for user-owned callback resources."""
    parts = callback_data.split(":")
    if not parts:
        return None

    campaign_indexes = {
        "campaign": 2,
        "database": 2,
        "compose": 2,
        "delays": 2,
        "mailing": 2,
        "stats": 2,
        "logs": 2,
    }
    if parts[0] in campaign_indexes:
        try:
            return "campaign", int(parts[campaign_indexes[parts[0]]])
        except (IndexError, ValueError):
            return None
    if parts[0] == "confirm" and len(parts) > 2 and parts[1] == "del_camp":
        try:
            return "campaign", int(parts[2])
        except ValueError:
            return None
    if parts[:3] == ["tools", "parser", "camp"]:
        try:
            return "campaign", int(parts[3])
        except (IndexError, ValueError):
            return None
    if parts[0] == "accounts" and len(parts) > 2 and parts[1] in {
        "view", "reconnect", "delete", "delete_confirm", "toggle"
    }:
        try:
            return "account", int(parts[2])
        except ValueError:
            return None
    return None


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
        referrer_id = None
        if isinstance(event, Message) and event.text:
            command_parts = event.text.split(maxsplit=1)
            if (
                command_parts[0].split("@", 1)[0] == "/start"
                and len(command_parts) == 2
                and command_parts[1].startswith("ref_")
            ):
                try:
                    candidate_id = int(command_parts[1].split("_", 1)[1])
                except ValueError:
                    candidate_id = None
                if candidate_id is not None:
                    referrer = await db.get_user_by_id(candidate_id)
                    if referrer and referrer.tg_id != tg_user.id:
                        referrer_id = candidate_id

        bot_user = await db.get_or_create_user(
            tg_user.id, username, full_name, referrer_id=referrer_id
        )

        if bot_user.is_banned:
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Ваш аккаунт заблокирован. Обратитесь в поддержку."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Аккаунт заблокирован.", show_alert=True)
            return

        data["bot_user"] = bot_user

        if isinstance(event, CallbackQuery) and event.data:
            resource = _callback_resource(event.data)
            if resource:
                resource_type, resource_id = resource
                if resource_type == "campaign":
                    campaign = await db.get_campaign(resource_id)
                    if not campaign or campaign.user_id != bot_user.id:
                        await event.answer("⛔ Нет доступа к этой рассылке.", show_alert=True)
                        return
                elif resource_type == "account":
                    account = await db.get_account(resource_id)
                    if not account or account.user_id != bot_user.id:
                        await event.answer("⛔ Нет доступа к этому аккаунту.", show_alert=True)
                        return

                if event.data.startswith("campaign:toggle_acc:"):
                    try:
                        account_id = int(event.data.split(":")[3])
                    except (IndexError, ValueError):
                        await event.answer("Некорректные данные кнопки.", show_alert=True)
                        return
                    account = await db.get_account(account_id)
                    if not account or account.user_id != bot_user.id:
                        await event.answer("⛔ Нет доступа к этому аккаунту.", show_alert=True)
                        return

        return await handler(event, data)
