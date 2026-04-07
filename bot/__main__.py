"""ESAS Telegram Bot — entry point."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import datetime

from bot.core.config import BOT_TOKEN, SHEETS_SYNC_INTERVAL
from bot.handlers.admin import router as admin_router
from bot.handlers.common import router as common_router
from bot.handlers.order import router as order_router
from bot.core.middlewares import (
    ActionLoggerMiddleware,
    ErrorMiddleware,
    ThrottlingMiddleware,
)
from bot.services.seed import init_db
from bot.services.sheets_sync import run_full_sync


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


async def _sheets_sync_loop(interval: int) -> None:
    """Фоновая задача: синх каждые N секунд."""
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_full_sync()
        except Exception as exc:
            logger.error("Sheets sync error: %s", exc)


async def _payment_expire_loop() -> None:
    """Автоотмена заявок awaiting_payment старше 1 часа."""
    from sqlalchemy import update
    from bot.core.database import async_session
    from bot.domain.models import Order

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(60)
        try:
            cutoff = datetime.datetime.now(
                tz=datetime.timezone.utc
            ) - datetime.timedelta(hours=1)
            async with async_session() as session:
                await session.execute(
                    update(Order)
                    .where(Order.status == "awaiting_payment")
                    .where(Order.created_at < cutoff)
                    .values(status="cancelled")
                )
                await session.commit()
        except Exception as exc:
            logger.error("Payment expire loop error: %s", exc)


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Initialising database …")
    await init_db()

    # Первичная синхронизация с Google Sheets.
    # Любая ошибка (сетевая или логическая) — бот не стартует.
    logger.info("Синхронизация с Google Sheets …")
    await run_full_sync()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Register middlewares  (outer → inner: Error → Throttle → Logger)
    for event_type in (dp.message, dp.callback_query):
        event_type.middleware(ErrorMiddleware())
        event_type.middleware(ThrottlingMiddleware())
        event_type.middleware(ActionLoggerMiddleware())

    # Register routers (common first — so menu-interrupts are caught)
    dp.include_router(common_router)
    dp.include_router(order_router)
    dp.include_router(admin_router)

    logger.info("Bot is starting …")
    try:
        # Стартуем фоновую синхронизацию и polling параллельно
        await asyncio.gather(
            _sheets_sync_loop(SHEETS_SYNC_INTERVAL),
            _payment_expire_loop(),
            dp.start_polling(bot),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
