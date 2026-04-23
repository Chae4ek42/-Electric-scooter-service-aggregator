"""Middlewares: action logging, throttling, error handling."""

from __future__ import annotations

import functools
import logging
import traceback
import time
from typing import Any, Awaitable, Callable
from unittest.mock import patch

from aiogram import BaseMiddleware, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

import redis.asyncio as aioredis

from client_bot.core.config import REDIS_URL, THROTTLE_RATE
from client_bot.core.database import async_session
from client_bot.domain.models import UserAction

logger = logging.getLogger(__name__)

_redis_pool: aioredis.Redis | None = None
_redis_available: bool | None = None  # None = not checked yet


async def _get_redis() -> aioredis.Redis | None:
    """Return Redis connection or None if unavailable."""
    global _redis_pool, _redis_available
    if _redis_available is False:
        return None
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(REDIS_URL, decode_responses=True)
    if _redis_available is None:
        try:
            await _redis_pool.ping()
            _redis_available = True
        except Exception:
            _redis_available = False
            logger.warning("Redis unavailable for throttling, using in-memory fallback")
            return None
    return _redis_pool


class _ResponseCapture:
    """Temporarily patches Bot methods to capture the first bot reply text."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._patches: list[Any] = []
        self.text: str | None = None
        self.response_type: str | None = None

    def _make_wrapper(self, original):
        capture = self

        @functools.wraps(original)
        async def wrapper(*args, **kwargs):
            result = await original(*args, **kwargs)
            if capture.text is None:
                # Extract text from positional or keyword args
                txt = kwargs.get("text") or (args[1] if len(args) > 1 else None)
                if txt:
                    capture.text = str(txt)[:500]
                    rm = kwargs.get("reply_markup")
                    if rm and hasattr(rm, "inline_keyboard"):
                        capture.response_type = "inline"
                    else:
                        capture.response_type = "text"
            return result

        return wrapper

    def install(self) -> None:
        for method_name in ("send_message", "edit_message_text"):
            original = getattr(self._bot, method_name)
            p = patch.object(self._bot, method_name, self._make_wrapper(original))
            p.start()
            self._patches.append(p)

    def uninstall(self) -> None:
        for p in self._patches:
            p.stop()
        self._patches.clear()


class ActionLoggerMiddleware(BaseMiddleware):
    """Логирует все пользовательские действия в БД и выводит подробные логи."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        username: str = ""
        action_type = "unknown"
        payload = ""
        state_str: str | None = None

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username or "" if event.from_user else ""
            if event.text:
                if event.text.startswith("/"):
                    action_type = "command"
                else:
                    action_type = "text_input"
                payload = event.text[:500]
            elif event.location:
                action_type = "location"
                payload = (
                    f"lat={event.location.latitude}, lon={event.location.longitude}"
                )
            elif event.contact:
                action_type = "contact"
            elif event.photo:
                action_type = "photo"
            elif event.document:
                action_type = "document"
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            username = event.from_user.username or "" if event.from_user else ""
            action_type = "button_click"
            payload = (event.data or "")[:500]

        if user_id is None:
            return await handler(event, data)

        fsm: FSMContext | None = data.get("state")
        if fsm:
            state_str = await fsm.get_state()

        # Wrap Bot methods to capture bot response
        bot: Bot | None = data.get("bot")
        capture: _ResponseCapture | None = None
        if bot:
            capture = _ResponseCapture(bot)
            capture.install()

        status = "success"
        error_context = None
        try:
            result = await handler(event, data)
        except Exception:
            status = "error"
            error_context = traceback.format_exc()[-1000:]
            raise
        finally:
            if capture:
                capture.uninstall()
            bot_response = capture.text if capture else None
            bot_response_type = capture.response_type if capture else None
            log_parts = [
                f"@{username}" if username else "",
                f"action={action_type}",
                f"state={state_str or 'none'}",
                f"payload={payload[:80]}" if payload else "",
                f"status={status}",
                f"response_type={bot_response_type}" if bot_response_type else "",
            ]
            log_msg = " | ".join(p for p in log_parts if p)
            if status == "error":
                logger.error(log_msg)
            else:
                logger.debug(log_msg)

            try:
                async with async_session() as session:
                    session.add(
                        UserAction(
                            user_id=user_id,
                            state=state_str,
                            action_type=action_type,
                            payload=payload,
                            status=status,
                            error_context=error_context,
                            bot_response=bot_response,
                            bot_response_type=bot_response_type,
                        )
                    )
                    await session.commit()
            except Exception:
                logger.exception("Failed to log user action to DB")

        return result


class ThrottlingMiddleware(BaseMiddleware):
    """Rate-limit per user (Redis with in-memory fallback)."""

    _KEY_PREFIX = "throttle:"

    def __init__(self, rate: float = THROTTLE_RATE) -> None:
        self._rate = rate
        self._last: dict[int, float] = {}  # in-memory fallback

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id if event.from_user else None

        if user_id is not None:
            now = time.time()
            r = await _get_redis()
            if r is not None:
                key = f"{self._KEY_PREFIX}{user_id}"
                last_str = await r.get(key)
                last = float(last_str) if last_str else 0.0
            else:
                last = self._last.get(user_id, 0.0)
            if now - last < self._rate:
                try:
                    async with async_session() as session:
                        fsm: FSMContext | None = data.get("state")
                        state_str = await fsm.get_state() if fsm else None
                        session.add(
                            UserAction(
                                user_id=user_id,
                                state=state_str,
                                action_type="flood_attempt",
                                payload="",
                                status="flood_attempt",
                            )
                        )
                        await session.commit()
                except Exception:
                    pass
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "Пожалуйста, не нажимайте кнопки так часто", show_alert=False
                    )
                return None
            if r is not None:
                await r.set(key, str(now), ex=max(1, int(self._rate) + 1))
            else:
                self._last[user_id] = now

        return await handler(event, data)


class ErrorMiddleware(BaseMiddleware):
    """Обрабатывает неожидаемые ошибки с подробным контекстом."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            user_id_ctx = ""
            if isinstance(event, (Message, CallbackQuery)) and event.from_user:
                user_id_ctx = f" user={event.from_user.id}"
            logger.exception("Unhandled error%s: %s", user_id_ctx, exc)
            try:
                user_id = None
                if isinstance(event, Message):
                    user_id = event.from_user.id if event.from_user else None
                    await event.answer(
                        "Произошла техническая ошибка. Пожалуйста, попробуйте позже"
                    )
                elif isinstance(event, CallbackQuery):
                    user_id = event.from_user.id if event.from_user else None
                    await event.answer(
                        "Произошла техническая ошибка. Пожалуйста, попробуйте позже",
                        show_alert=True,
                    )

                if user_id:
                    async with async_session() as session:
                        session.add(
                            UserAction(
                                user_id=user_id,
                                state=None,
                                action_type="system_error",
                                payload="",
                                status="system_error",
                                error_context=traceback.format_exc()[-1000:],
                            )
                        )
                        await session.commit()
            except Exception:
                logger.exception("Failed to handle error gracefully")
            return None
