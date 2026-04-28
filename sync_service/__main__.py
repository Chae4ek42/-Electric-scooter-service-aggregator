from __future__ import annotations

import asyncio
import logging

from client_bot.core.config import SHEETS_SYNC_INTERVAL
from client_bot.core.logging_setup import setup_logging
from client_bot.services.seed import init_db
from client_bot.services.sheets_sync import run_full_sync


async def _sync_loop(interval: int) -> None:
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_full_sync()
        except Exception as exc:
            logger.error("Sheets sync error: %s", exc)


async def main() -> None:
    setup_logging(service_name="sync-service")
    logger = logging.getLogger(__name__)

    logger.info("Initialising database …")
    await init_db()

    logger.info("Running integrations health-check (dry-run) …")
    await run_full_sync(first_run=True, dry_run=True)

    logger.info("Running initial sync …")
    await run_full_sync(first_run=True)

    logger.info(
        "Sync service started (interval=%ds) …",
        SHEETS_SYNC_INTERVAL,
    )
    await _sync_loop(SHEETS_SYNC_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
