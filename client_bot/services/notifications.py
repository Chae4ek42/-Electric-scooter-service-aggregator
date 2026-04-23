from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot

logger = logging.getLogger("esas.tech.notifications")

_DEDUPE_TTL_SEC = 180
_dedupe_cache: dict[str, float] = {}


def _prune_dedupe_cache(now: float) -> None:
    expired = [k for k, ts in _dedupe_cache.items() if now - ts > _DEDUPE_TTL_SEC]
    for key in expired:
        _dedupe_cache.pop(key, None)


def _register_dedupe_key(dedupe_key: str | None) -> bool:
    if not dedupe_key:
        return True
    now = time.monotonic()
    _prune_dedupe_cache(now)
    if dedupe_key in _dedupe_cache:
        return False
    _dedupe_cache[dedupe_key] = now
    return True


async def send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup: Any | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
    base_delay_sec: float = 0.4,
) -> bool:
    if not _register_dedupe_key(dedupe_key):
        logger.info("NOTIFY_DEDUPED | key=%s", dedupe_key)
        return False

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
            return True
        except (
            Exception
        ) as exc:  # pragma: no cover - behavior verified with monkeypatch tests
            last_error = exc
            if attempt == max_attempts:
                break
            delay = base_delay_sec * (2 ** (attempt - 1))
            logger.warning(
                "NOTIFY_RETRY | attempt=%s | chat_id=%s | error=%s",
                attempt,
                chat_id,
                exc,
            )
            await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error
    return False


async def send_by_token(
    token: str,
    chat_id: int,
    text: str,
    *,
    reply_markup: Any | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> bool:
    bot = Bot(token=token)
    try:
        return await send_with_retry(
            bot,
            chat_id,
            text,
            reply_markup=reply_markup,
            dedupe_key=dedupe_key,
            max_attempts=max_attempts,
        )
    finally:
        await bot.session.close()
