from __future__ import annotations

import asyncio
import logging

from client_bot.core.logging_setup import setup_logging
from client_bot.core.metrics import start_metrics_server
from client_bot.core.resilience import (
    install_runtime_exception_handlers,
    register_runtime_error,
)
from client_bot.services.seed import init_db
from client_bot.services.sheets_sync import run_full_sync
from client_bot.services.sheets_events import (
    SheetsRetryWorker,
    trigger_sheets_sync,
)


async def main() -> None:
    setup_logging(service_name="sync-service")
    logger = logging.getLogger(__name__)
    install_runtime_exception_handlers(service_name="sync-service", logger=logger)
    start_metrics_server(service_name="sync-service", default_port=9103)

    logger.info("Initialising database …")
    await init_db()

    logger.info("Running integrations health-check (dry-run) …")
    await run_full_sync(first_run=True, dry_run=True)

    logger.info("Running initial sync …")
    await trigger_sheets_sync(source="startup:sync-service")

    retry_worker = SheetsRetryWorker(service_name="sync-service")
    await retry_worker.start()

    logger.info("Sync service started in event-driven mode (retry worker active) …")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await register_runtime_error(
            action_type="sync_service_runtime_error",
            error=exc,
            payload="sync-service",
        )
        raise
    finally:
        await retry_worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
