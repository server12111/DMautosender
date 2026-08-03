import asyncio
import logging
import math

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
    if config.DEFAULT_TRIAL_DAYS <= 0:
        raise RuntimeError("DEFAULT_TRIAL_DAYS должен быть положительным")
    if (
        not math.isfinite(config.DEFAULT_PRO_PRICE_USD)
        or config.DEFAULT_PRO_PRICE_USD <= 0
        or not math.isfinite(config.DEFAULT_BUSINESS_PRICE_USD)
        or config.DEFAULT_BUSINESS_PRICE_USD <= 0
    ):
        raise RuntimeError("Цены тарифов по умолчанию должны быть положительными")

    logger.info("Инициализация AutoSender DM SaaS...")

    db = Database(config.DATABASE_PATH)
    bot: Bot | None = None
    manager: UserbotManager | None = None
    storage: MemoryStorage | None = None
    try:
        await db.connect()
        await db.recover_interrupted_mailings()

        runtime_settings = await db.get_all_bot_settings()
        for setting_key, config_attr in {
            "platega_merchant_id": "PLATEGA_MERCHANT_ID",
            "platega_secret": "PLATEGA_SECRET",
            "cryptobot_token": "CRYPTOBOT_TOKEN",
            "ton_wallet": "TON_WALLET",
        }.items():
            if runtime_settings.get(setting_key):
                setattr(config, config_attr, runtime_settings[setting_key])
        logger.info("База данных подключена: %s", config.DATABASE_PATH)

        bot = Bot(
            token=config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            bot_info = await bot.get_me()
            if bot_info.username:
                await db.set_bot_setting("bot_username", bot_info.username)
        except Exception:
            logger.warning(
                "Не удалось обновить username бота для реферальных ссылок",
                exc_info=True,
            )

        manager = UserbotManager(db, config.SESSIONS_PATH)
        await manager.start_all()

        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)

        # ── Global error handler ──────────────────────────────────────────────
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
                    await update.message.answer(
                        "⚠️ Произошла внутренняя ошибка. Попробуйте /start"
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        "⚠️ Ошибка. Попробуйте /start", show_alert=True
                    )
            except Exception:
                pass
            return True

        # ── Routers setup ─────────────────────────────────────────────────────
        user_router, admin_router = setup_routers(config.ADMIN_IDS)

        user_mw = UserMiddleware()
        dp.message.middleware(user_mw)
        dp.callback_query.middleware(user_mw)

        admin_mw = AdminMiddleware(config.ADMIN_IDS)
        admin_router.message.middleware(admin_mw)
        admin_router.callback_query.middleware(admin_mw)

        dp.include_router(user_router)
        dp.include_router(admin_router)

        dp["db"] = db
        dp["manager"] = manager

        all_accounts = await db.get_all_active_accounts()
        logger.info(
            "Бот запущен. Администраторов: %d, активных аккаунтов в БД: %d",
            len(config.ADMIN_IDS),
            len(all_accounts),
        )

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        from .handlers.mailing import stop_all_senders

        try:
            await stop_all_senders()
        except Exception:
            logger.exception("Не удалось остановить активные рассылки")
        if manager:
            try:
                await manager.disconnect_all()
            except Exception:
                logger.exception("Не удалось отключить Telegram-клиенты")
        if storage:
            try:
                await storage.close()
            except Exception:
                logger.exception("Не удалось закрыть FSM-хранилище")
        if bot:
            try:
                await bot.session.close()
            except Exception:
                logger.exception("Не удалось закрыть сессию бота")
        try:
            await db.close()
        except Exception:
            logger.exception("Не удалось закрыть базу данных")
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    import os
    import sys
    import time
    import traceback
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _RESTART_DELAY = 5
    while True:
        try:
            asyncio.run(main())
            break
        except (KeyboardInterrupt, SystemExit):
            print("Остановлено пользователем.")
            break
        except Exception:
            print(f"\n=== КРАШ ===\n{traceback.format_exc()}")
            print(f"Перезапуск через {_RESTART_DELAY} сек...")
            time.sleep(_RESTART_DELAY)
            os.execv(
                sys.executable,
                [sys.executable, "-m", "bot.main", *sys.argv[1:]],
            )
