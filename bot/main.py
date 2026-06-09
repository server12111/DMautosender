import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from .config import config
from .database.db import Database
from .middlewares.admin import AdminMiddleware
from .middlewares.user import UserMiddleware
from .userbot.manager import UserbotManager
from .handlers import setup_routers
from .utils.logger import setup_logging

logger = logging.getLogger("dmsender")


async def main() -> None:
    config.ensure_dirs()
    setup_logging(config.LOGS_PATH)

    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env файле!")
    if not config.ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS не задан в .env файле!")

    logger.info("Инициализация DMautosender SaaS...")

    db = Database(config.DATABASE_PATH)
    await db.connect()
    logger.info("База данных подключена: %s", config.DATABASE_PATH)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    manager = UserbotManager(db, config.SESSIONS_PATH)
    await manager.start_all()

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # ── Global error handler ──────────────────────────────────────────────────
    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        logger.error(
            "Необработанная ошибка в хендлере: %s",
            event.exception,
            exc_info=event.exception,
        )
        update = event.update
        try:
            if update.message:
                await update.message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте /start")
            elif update.callback_query:
                await update.callback_query.answer("⚠️ Ошибка. Попробуйте /start", show_alert=True)
        except Exception:
            pass
        return True

    # ── Routers setup ─────────────────────────────────────────────────────────
    user_router, admin_router = setup_routers(config.ADMIN_IDS)

    # UserMiddleware — registers all users in DB, checks bans
    # Applies globally to all messages and callbacks
    user_mw = UserMiddleware()
    dp.message.middleware(user_mw)
    dp.callback_query.middleware(user_mw)

    # AdminMiddleware — checks admin IDs; only applied to admin router
    admin_mw = AdminMiddleware(config.ADMIN_IDS)
    admin_router.message.middleware(admin_mw)
    admin_router.callback_query.middleware(admin_mw)

    dp.include_router(user_router)
    dp.include_router(admin_router)

    # ── Inject dependencies ───────────────────────────────────────────────────
    dp["db"] = db
    dp["manager"] = manager

    all_accounts = await db.get_all_active_accounts()
    logger.info(
        "Бот запущен. Администраторов: %d, активных аккаунтов в БД: %d",
        len(config.ADMIN_IDS),
        len(all_accounts),
    )

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await manager.disconnect_all()
        await db.close()
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
