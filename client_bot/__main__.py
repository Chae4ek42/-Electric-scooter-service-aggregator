"""ESAS Telegram Bot — entry point."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
import datetime

from client_bot.core.config import CLIENT_BOT_TOKEN, REDIS_URL
from client_bot.handlers.admin import router as admin_router
from client_bot.handlers.common import router as common_router
from client_bot.handlers.order import router as order_router
from client_bot.core.logging_setup import setup_logging
from client_bot.core.resilience import (
    install_runtime_exception_handlers,
    register_runtime_error,
)
from client_bot.core.middlewares import (
    ActionLoggerMiddleware,
    ErrorMiddleware,
    LogContextMiddleware,
    ThrottlingMiddleware,
)
from client_bot.services.fsm_reminder import FSMActivityMiddleware, fsm_reminder_loop
from client_bot.services.seed import init_db


async def _payment_expire_loop() -> None:
    """Автоотмена заявок awaiting_payment старше 1 часа."""

    from client_bot.core.database import async_session
    from client_bot.services.order_lifecycle import cancel_expired_awaiting_payment

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(60)
        try:
            cutoff = datetime.datetime.now(
                tz=datetime.timezone.utc
            ) - datetime.timedelta(hours=1)
            async with async_session() as session:
                changed = await cancel_expired_awaiting_payment(session, cutoff)
                if changed:
                    await session.commit()
                    logger.info("Payment expire loop cancelled %s orders", changed)
        except Exception as exc:
            logger.exception("Payment expire loop error")
            await register_runtime_error(
                action_type="payment_expire_loop_error",
                error=exc,
                payload="client-bot",
            )


async def _make_storage(logger):
    """Redis storage with fallback to MemoryStorage."""
    from aiogram.fsm.storage.memory import MemoryStorage

    try:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(REDIS_URL)
        # Verify connection
        ping = await storage.redis.ping()
        if ping:
            logger.info("FSM storage: Redis (%s)", REDIS_URL)
            return storage
    except Exception as exc:
        logger.warning("Redis unavailable (%s), using MemoryStorage", exc)
    return MemoryStorage()


async def main() -> None:
    setup_logging(service_name="client-bot")
    logger = logging.getLogger(__name__)
    install_runtime_exception_handlers(service_name="client-bot", logger=logger)

    logger.info("Initialising database …")
    await init_db()

    bot = Bot(
        token=CLIENT_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = await _make_storage(logger)
    dp = Dispatcher(storage=storage)

    # Register middlewares (outer -> inner: context -> error -> throttle -> logger)
    for event_type in (dp.message, dp.callback_query):
        event_type.middleware(LogContextMiddleware())
        event_type.middleware(ErrorMiddleware())
        event_type.middleware(ThrottlingMiddleware())
        event_type.middleware(ActionLoggerMiddleware())
        event_type.middleware(
            FSMActivityMiddleware("client", form_state_prefixes=["OrderFSM:"])
        )

    # Register routers (common first — so menu-interrupts are caught)
    dp.include_router(common_router)
    dp.include_router(order_router)
    dp.include_router(admin_router)

    logger.info("Bot is starting …")
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="admin", description="Панель администратора"),
            BotCommand(command="client", description="Вернуться в главное меню"),
            BotCommand(command="health", description="Проверка инфраструктуры"),
        ]
    )
    try:
        # Стартуем фоновую синхронизацию и polling параллельно
        await asyncio.gather(
            _payment_expire_loop(),
            fsm_reminder_loop(bot, "client"),
            dp.start_polling(bot),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
