from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence


async def run_with_retry(
    *,
    action_name: str,
    operation: Callable[[], Awaitable[None]],
    logger: logging.Logger,
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    on_error: Callable[..., Awaitable[None]] | None = None,
    raise_on_fail: bool = False,
) -> bool:
    """Run async operation with exponential backoff retries."""
    if attempts < 1:
        attempts = 1

    delay = max(base_delay, 0.1)

    for attempt in range(1, attempts + 1):
        try:
            await operation()
            if attempt > 1:
                logger.info(
                    "%s succeeded on attempt %d/%d",
                    action_name,
                    attempt,
                    attempts,
                )
            return True
        except Exception as exc:
            logger.warning(
                "%s failed on attempt %d/%d: %s",
                action_name,
                attempt,
                attempts,
                exc,
            )
            if on_error is not None:
                try:
                    await on_error(
                        action_type=f"{action_name}_retry_error",
                        error=exc,
                        payload=f"attempt={attempt}/{attempts}",
                    )
                except Exception:
                    logger.exception("Failed to register retry error for %s", action_name)

            if attempt >= attempts:
                logger.error("%s failed after %d attempts", action_name, attempts)
                if raise_on_fail:
                    raise
                return False

            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 2)

    return False


async def wait_or_stop(stop_event: asyncio.Event, timeout: float) -> bool:
    """Wait for stop event with timeout. Returns True if stop requested."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        return True
    except TimeoutError:
        return False


async def cancel_background_tasks(
    tasks: Sequence[asyncio.Task],
    *,
    logger: logging.Logger,
    scope: str,
) -> None:
    if not tasks:
        return

    for task in tasks:
        if not task.done():
            task.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, Exception):
            logger.warning(
                "Background task finished with error during shutdown | scope=%s | task=%s | err=%s",
                scope,
                task.get_name(),
                result,
            )
