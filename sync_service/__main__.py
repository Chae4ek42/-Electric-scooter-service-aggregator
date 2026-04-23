from __future__ import annotations

import asyncio
import logging
import sys

from client_bot.core.config import SHEETS_SYNC_INTERVAL
from client_bot.services.seed import init_db
from client_bot.services.sheets_sync import run_full_sync


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


async def _sync_loop(interval: int) -> None:
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_full_sync()
        except Exception as exc:
            logger.error("Sheets sync error: %s", exc)


async def main() -> None:
    _setup_logging()
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
