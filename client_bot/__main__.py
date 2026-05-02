"""ESAS Telegram Bot — entry point."""

from __future__ import annotations

import asyncio
import datetime
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from client_bot.core.config import CLIENT_BOT_TOKEN, REDIS_URL
from client_bot.core.metrics import start_metrics_server
from client_bot.handlers.admin import router as admin_router
from client_bot.handlers.common import router as common_router
from client_bot.handlers.order import router as order_router
from client_bot.core.logging_setup import setup_logging
from client_bot.core.resilience import (
    create_guarded_task,
    install_runtime_exception_handlers,
    register_runtime_error,
)
from client_bot.core.startup import (
    cancel_background_tasks,
    run_with_retry,
    wait_or_stop,
)
from client_bot.core.middlewares import (
    ActionLoggerMiddleware,
    ErrorMiddleware,
    LogContextMiddleware,
    ThrottlingMiddleware,
)
from client_bot.services.fsm_reminder import FSMActivityMiddleware, fsm_reminder_loop
from client_bot.services.sheets_events import (
    start_event_driven_sync,
    stop_event_driven_sync,
)
from client_bot.services.seed import init_db


async def _payment_expire_loop(stop_event: asyncio.Event) -> None:
    """Автоотмена заявок awaiting_payment старше 1 часа."""

    from client_bot.core.database import async_session
    from client_bot.services.order_lifecycle import cancel_expired_awaiting_payment

    logger = logging.getLogger(__name__)
    while not stop_event.is_set():
        if await wait_or_stop(stop_event, 60):
            break
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


async def _set_commands(bot: Bot, logger: logging.Logger) -> None:
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="admin", description="Панель администратора"),
        BotCommand(command="client", description="Вернуться в главное меню"),
        BotCommand(command="health", description="Проверка инфраструктуры"),
    ]

    async def _op() -> None:
        await bot.set_my_commands(commands)

    await run_with_retry(
        action_name="client_set_my_commands",
        operation=_op,
        logger=logger,
        attempts=6,
        base_delay=1.0,
        max_delay=20.0,
        on_error=register_runtime_error,
        raise_on_fail=False,
    )


async def _run_polling_with_backoff(
    dp: Dispatcher,
    bot: Bot,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    delay = 2.0
    while not stop_event.is_set():
        try:
            await dp.start_polling(bot, handle_signals=False)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Polling failed, will retry")
            await register_runtime_error(
                action_type="client_polling_error",
                error=exc,
                payload="client-bot",
            )
            if await wait_or_stop(stop_event, delay):
                return
            delay = min(delay * 2, 60.0)


async def main() -> None:
    setup_logging(service_name="client-bot")
    logger = logging.getLogger(__name__)
    install_runtime_exception_handlers(service_name="client-bot", logger=logger)
    start_metrics_server(service_name="client-bot", default_port=9101)

    logger.info("Initialising database …")
    await init_db()

    bot = Bot(
        token=CLIENT_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = await _make_storage(logger)
    dp = Dispatcher(storage=storage)
    stop_event = asyncio.Event()
    background_tasks: list[asyncio.Task] = []

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
    await _set_commands(bot, logger)

    event_task = await start_event_driven_sync(service_name="client-bot")
    if event_task is not None:
        background_tasks.append(event_task)

    background_tasks.append(
        create_guarded_task(
            _payment_expire_loop(stop_event),
            logger=logger,
            task_name="client_payment_expire_loop",
            action_type="client_payment_expire_loop_error",
            payload="client-bot",
        )
    )
    background_tasks.append(
        create_guarded_task(
            fsm_reminder_loop(bot, "client"),
            logger=logger,
            task_name="client_fsm_reminder_loop",
            action_type="client_fsm_reminder_loop_error",
            payload="client-bot",
        )
    )

    try:
        await _run_polling_with_backoff(dp, bot, stop_event, logger)
    finally:
        stop_event.set()
        try:
            await dp.stop_polling()
        except Exception:
            pass
        await stop_event_driven_sync()
        await cancel_background_tasks(
            background_tasks,
            logger=logger,
            scope="client-bot",
        )
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
