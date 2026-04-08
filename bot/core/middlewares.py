"""Middlewares: action logging, throttling, error handling."""

from __future__ import annotations

import logging
import traceback
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.core.config import THROTTLE_RATE
from bot.core.database import async_session
from bot.domain.models import UserAction

logger = logging.getLogger(__name__)


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

        start_ts = time.monotonic()
        status = "success"
        error_context = None
        try:
            result = await handler(event, data)
        except Exception:
            status = "error"
            error_context = traceback.format_exc()[-1000:]
            raise
        finally:
            elapsed_ms = (time.monotonic() - start_ts) * 1000
            log_parts = [
                f"user={user_id}",
                f"@{username}" if username else "",
                f"action={action_type}",
                f"state={state_str or 'none'}",
                f"payload={payload[:80]}" if payload else "",
                f"status={status}",
                f"elapsed={elapsed_ms:.0f}ms",
            ]
            log_msg = " | ".join(p for p in log_parts if p)
            if status == "error":
                logger.error("ACTION %s", log_msg)
            else:
                logger.info("ACTION %s", log_msg)

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
                        )
                    )
                    await session.commit()
            except Exception:
                logger.exception("Failed to log user action to DB")

        return result


class ThrottlingMiddleware(BaseMiddleware):
    """Rate-limit per user (in-memory)."""

    def __init__(self, rate: float = THROTTLE_RATE) -> None:
        self._rate = rate
        self._last: dict[int, float] = {}

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
            now = time.monotonic()
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
