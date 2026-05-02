from __future__ import annotations

import logging
from typing import Any

from client_bot.core.database import async_session
from client_bot.services.notification_settings import get_admin_recipients_for_event
from client_bot.services.notifications import send_with_retry

logger = logging.getLogger(__name__)


async def notify_admins(
    bot: Any,
    *,
    scope: str,
    event_key: str,
    text: str,
    dedupe_prefix: str,
) -> None:
    async with async_session() as session:
        recipients = await get_admin_recipients_for_event(
            session,
            scope=scope,
            event_key=event_key,
        )

    for admin in recipients:
        try:
            await send_with_retry(
                bot,
                admin.id,
                text,
                dedupe_key=f"{dedupe_prefix}:{admin.id}",
            )
        except Exception:
            logger.warning(
                "Failed to notify admin %s | scope=%s | event=%s",
                admin.id,
                scope,
                event_key,
            )
