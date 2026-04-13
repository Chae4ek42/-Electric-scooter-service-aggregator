"""30-minute inactivity reminder for incomplete FSM forms."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

logger = logging.getLogger(__name__)

REMINDER_SECONDS = 30 * 60  # 30 minutes
CHECK_INTERVAL = 60  # check every minute

# {(bot_token_hash, user_id): last_activity_timestamp}
_activity: Dict[tuple, float] = {}
# Track already-reminded users to avoid spam
_reminded: set[tuple] = set()

_REMINDER_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Продолжить", callback_data="fsm_remind:continue"
            ),
            InlineKeyboardButton(text="Отменить", callback_data="fsm_remind:cancel"),
        ]
    ]
)


class FSMActivityMiddleware(BaseMiddleware):
    """Records last-activity timestamp for users with active FSM state."""

    def __init__(self, bot_key: str) -> None:
        self._bot_key = bot_key

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        state: FSMContext | None = data.get("state")
        if user and state:
            current = await state.get_state()
            key = (self._bot_key, user.id)
            if current:
                _activity[key] = time.monotonic()
                _reminded.discard(key)
            else:
                _activity.pop(key, None)
                _reminded.discard(key)

        return await handler(event, data)


async def fsm_reminder_loop(bot: Bot, bot_key: str) -> None:
    """Background loop: send reminders after 30 min of inactivity."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = time.monotonic()
        stale_keys = []
        for key, ts in list(_activity.items()):
            bk, user_id = key
            if bk != bot_key:
                continue
            if now - ts >= REMINDER_SECONDS and key not in _reminded:
                stale_keys.append(key)

        for key in stale_keys:
            _, user_id = key
            try:
                await bot.send_message(
                    user_id,
                    "Вы не завершили заполнение формы. " "Продолжите или отмените.",
                    reply_markup=_REMINDER_KB,
                )
                _reminded.add(key)
                logger.info("Sent inactivity reminder to user %s", user_id)
            except Exception:
                logger.debug("Could not send reminder to %s", user_id, exc_info=True)
                # Remove stale entry
                _activity.pop(key, None)
