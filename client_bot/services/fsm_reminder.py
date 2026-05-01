"""30-minute inactivity reminder for incomplete FSM forms."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Sequence

from aiogram import BaseMiddleware, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from client_bot.core.config import app_config
from client_bot.core.resilience import register_runtime_error

logger = logging.getLogger(__name__)

REMINDER_SECONDS: int = app_config.fsm_reminder.timeout
CHECK_INTERVAL: int = app_config.fsm_reminder.check_interval

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
    """Records last-activity timestamp only for users in form-filling FSM states."""

    def __init__(self, bot_key: str, form_state_prefixes: Sequence[str] = ()) -> None:
        self._bot_key = bot_key
        self._prefixes = tuple(form_state_prefixes)

    def _is_form_state(self, state: str) -> bool:
        if not self._prefixes:
            return True  # fallback: track all states
        return any(state.startswith(p) for p in self._prefixes)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)

        result = await handler(event, data)

        state: FSMContext | None = data.get("state")
        if user and state:
            current = await state.get_state()
            key = (self._bot_key, user.id)
            if current and self._is_form_state(current):
                _activity[key] = time.monotonic()
                _reminded.discard(key)
            else:
                _activity.pop(key, None)
                _reminded.discard(key)

        return result


async def fsm_reminder_loop(bot: Bot, bot_key: str) -> None:
    """Background loop: send reminders after 30 min of inactivity."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
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
                except Exception as exc:
                    logger.warning(
                        "Could not send reminder to %s",
                        user_id,
                        exc_info=True,
                    )
                    await register_runtime_error(
                        action_type="fsm_reminder_send_error",
                        error=exc,
                        user_id=user_id,
                        payload=f"bot={bot_key}",
                        status="warning",
                    )
                    # Remove stale entry
                    _activity.pop(key, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("FSM reminder loop iteration failed | bot=%s", bot_key)
            await register_runtime_error(
                action_type="fsm_reminder_loop_error",
                error=exc,
                payload=f"bot={bot_key}",
            )
