"""ESAS Partner Bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from bot.core.config import PARTNER_BOT_TOKEN, REDIS_URL
from bot.core.middlewares import (
    ActionLoggerMiddleware,
    ErrorMiddleware,
    ThrottlingMiddleware,
)
from bot.services.fsm_reminder import FSMActivityMiddleware, fsm_reminder_loop
from bot.services.seed import init_db

from partner_bot.handlers.common import router as common_router
from partner_bot.handlers.registration import router as registration_router
from partner_bot.handlers.orders import router as orders_router
from partner_bot.handlers.profile import router as profile_router
from partner_bot.handlers.notifications import router as notif_router
from partner_bot.handlers.admin import router as admin_router


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def _pause_reopen_loop() -> None:
    """Auto-reopen services whose pause_until has passed."""
    import datetime
    from sqlalchemy import select, update
    from bot.core.database import async_session
    from bot.domain.models import Service

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            async with async_session() as session:
                expired = (
                    (
                        await session.execute(
                            select(Service)
                            .where(Service.is_available.is_(False))
                            .where(Service.pause_until.isnot(None))
                            .where(Service.pause_until <= now)
                        )
                    )
                    .scalars()
                    .all()
                )
                for svc in expired:
                    svc.is_available = True
                    svc.pause_until = None
                    logger.info("Auto-reopened service %s (id=%s)", svc.name, svc.id)
                if expired:
                    await session.commit()
        except Exception as exc:
            logger.error("Pause reopen loop error: %s", exc)


async def _make_storage(logger):
    """Redis storage with fallback to MemoryStorage."""
    from aiogram.fsm.storage.memory import MemoryStorage

    try:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(REDIS_URL)
        ping = await storage.redis.ping()
        if ping:
            logger.info("FSM storage: Redis (%s)", REDIS_URL)
            return storage
    except Exception as exc:
        logger.warning("Redis unavailable (%s), using MemoryStorage", exc)
    return MemoryStorage()


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    if not PARTNER_BOT_TOKEN:
        logger.error("PARTNER_BOT_TOKEN not set")
        return

    logger.info("Initialising database ...")
    await init_db()

    bot = Bot(
        token=PARTNER_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = await _make_storage(logger)
    dp = Dispatcher(storage=storage)

    for event_type in (dp.message, dp.callback_query):
        event_type.middleware(ErrorMiddleware())
        event_type.middleware(ThrottlingMiddleware())
        event_type.middleware(ActionLoggerMiddleware())
        event_type.middleware(
            FSMActivityMiddleware("partner", form_state_prefixes=["RegistrationFSM:"])
        )

    dp.include_router(admin_router)
    dp.include_router(common_router)
    dp.include_router(registration_router)
    dp.include_router(orders_router)
    dp.include_router(profile_router)
    dp.include_router(notif_router)

    logger.info("Partner bot is starting ...")
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="admin", description="Панель администратора"),
            BotCommand(command="client", description="Режим партнёра"),
        ]
    )
    try:
        await asyncio.gather(
            fsm_reminder_loop(bot, "partner"),
            _pause_reopen_loop(),
            dp.start_polling(bot),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
