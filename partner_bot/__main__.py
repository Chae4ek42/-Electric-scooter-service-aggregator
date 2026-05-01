"""ESAS Partner Bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import zoneinfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from client_bot.core.config import PARTNER_BOT_TOKEN, REDIS_URL
from client_bot.core.logging_setup import setup_logging
from client_bot.core.resilience import (
    create_guarded_task,
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

from partner_bot.handlers.common import router as common_router
from partner_bot.handlers.registration import router as registration_router
from partner_bot.handlers.orders import router as orders_router
from partner_bot.handlers.profile import router as profile_router
from partner_bot.handlers.notifications import router as notif_router
from partner_bot.handlers.admin import router as admin_router

_PAUSE_REOPEN_POLL_SECONDS = 15
try:
    _MSK = zoneinfo.ZoneInfo("Europe/Moscow")
except Exception:
    _MSK = None


async def _pause_reopen_loop() -> None:
    """Auto-reopen services whose pause_until has passed."""
    import datetime
    from sqlalchemy import select
    from client_bot.core.database import async_session
    from client_bot.domain.models import Service
    from client_bot.services.sheets_writer import set_service_available

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(_PAUSE_REOPEN_POLL_SECONDS)
        try:
            now = (
                datetime.datetime.now(tz=_MSK)
                if _MSK is not None
                else datetime.datetime.now(tz=datetime.timezone.utc)
            )
            reopened_ids: list[int] = []
            async with async_session() as session:
                paused = (
                    (
                        await session.execute(
                            select(Service)
                            .where(Service.is_available.is_(False))
                            .where(Service.pause_until.isnot(None))
                        )
                    )
                    .scalars()
                    .all()
                )

                for svc in paused:
                    pause_until = svc.pause_until
                    if pause_until is None:
                        continue
                    if pause_until.tzinfo is None:
                        if _MSK is not None:
                            pause_until = pause_until.replace(tzinfo=_MSK)
                        else:
                            pause_until = pause_until.replace(
                                tzinfo=datetime.timezone.utc
                            )
                    else:
                        pause_until = pause_until.astimezone(now.tzinfo)

                    if pause_until > now:
                        continue

                    svc.is_available = True
                    svc.pause_until = None
                    reopened_ids.append(svc.id)
                    logger.info("Auto-reopened service %s (id=%s)", svc.name, svc.id)

                if reopened_ids:
                    await session.commit()

            for service_id in reopened_ids:
                create_guarded_task(
                    asyncio.to_thread(set_service_available, service_id, True),
                    logger=logger,
                    task_name=f"pause_reopen_sheets:{service_id}",
                    action_type="pause_reopen_sheet_error",
                    payload=f"service_id={service_id}",
                )
        except Exception as exc:
            logger.exception("Pause reopen loop error")
            await register_runtime_error(
                action_type="pause_reopen_loop_error",
                error=exc,
                payload="partner-bot",
            )


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
    setup_logging(service_name="partner-bot")
    logger = logging.getLogger(__name__)
    install_runtime_exception_handlers(service_name="partner-bot", logger=logger)

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
        event_type.middleware(LogContextMiddleware())
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
            BotCommand(command="health", description="Проверка инфраструктуры"),
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
