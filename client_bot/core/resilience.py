from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from typing import Any, Coroutine

from client_bot.core.database import async_session
from client_bot.core.metrics import mark_runtime_error
from client_bot.domain.models import UserAction

logger = logging.getLogger(__name__)

_SYSTEM_USER_ID = 0
_MAX_ACTION_LEN = 50
_MAX_STATUS_LEN = 30
_MAX_PAYLOAD_LEN = 500
_MAX_ERROR_LEN = 4000

_installed_hooks = False
_runtime_loop: asyncio.AbstractEventLoop | None = None


def _cut(value: str | None, limit: int) -> str:
    if not value:
        return ""
    text = str(value)
    return text[:limit]


def _error_text(error: BaseException | str | None) -> str:
    if error is None:
        return ""
    if isinstance(error, BaseException):
        return _cut(
            "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
            _MAX_ERROR_LEN,
        )
    return _cut(str(error), _MAX_ERROR_LEN)


async def register_runtime_error(
    *,
    action_type: str,
    error: BaseException | str | None = None,
    user_id: int | None = None,
    state: str | None = None,
    payload: str | None = None,
    status: str = "system_error",
) -> None:
    """Persist runtime/system errors in user_actions for postmortem analysis."""
    safe_user_id = user_id if user_id is not None else _SYSTEM_USER_ID
    safe_action = _cut(action_type, _MAX_ACTION_LEN) or "system_error"
    safe_status = _cut(status, _MAX_STATUS_LEN) or "system_error"
    safe_payload = _cut(payload, _MAX_PAYLOAD_LEN)
    safe_error = _error_text(error)

    mark_runtime_error(safe_action)

    try:
        async with async_session() as session:
            session.add(
                UserAction(
                    user_id=safe_user_id,
                    state=state,
                    action_type=safe_action,
                    payload=safe_payload,
                    status=safe_status,
                    error_context=safe_error,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "Failed to persist runtime error | action=%s | user=%s",
            safe_action,
            safe_user_id,
        )


def _enqueue_error_registration_from_any_thread(
    *,
    action_type: str,
    error: BaseException | str | None = None,
    payload: str | None = None,
) -> None:
    loop = _runtime_loop
    if loop is None or not loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(
            register_runtime_error(
                action_type=action_type,
                error=error,
                payload=payload,
            ),
            loop,
        )
    except Exception:
        logger.exception("Failed to enqueue runtime error registration")


def create_guarded_task(
    coro: Coroutine[Any, Any, Any],
    *,
    logger: logging.Logger,
    task_name: str,
    action_type: str = "background_task_error",
    payload: str | None = None,
    user_id: int | None = None,
) -> asyncio.Task[Any]:
    """Create a task that always logs and registers unhandled exceptions."""
    task = asyncio.create_task(coro, name=task_name)

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        if done_task.cancelled():
            return
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        except Exception as cb_exc:
            logger.exception(
                "Task completion callback failed | task=%s",
                task_name,
                exc_info=cb_exc,
            )
            return

        if exc is None:
            return

        details = _cut(payload or "", _MAX_PAYLOAD_LEN)
        logger.exception(
            "Background task failed | task=%s | payload=%s",
            task_name,
            details,
            exc_info=exc,
        )
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                raise RuntimeError("event loop is closed")
            loop.create_task(
                register_runtime_error(
                    action_type=action_type,
                    error=exc,
                    user_id=user_id,
                    payload=f"task={task_name}; {details}".strip(),
                )
            )
        except RuntimeError:
            _enqueue_error_registration_from_any_thread(
                action_type=action_type,
                error=exc,
                payload=f"task={task_name}; {details}".strip(),
            )

    task.add_done_callback(_on_done)
    return task


def install_runtime_exception_handlers(
    *,
    service_name: str,
    logger: logging.Logger,
) -> None:
    """Install process and asyncio exception hooks once per process."""
    global _installed_hooks, _runtime_loop
    if _installed_hooks:
        return

    _installed_hooks = True
    _runtime_loop = asyncio.get_running_loop()

    previous_asyncio_handler = _runtime_loop.get_exception_handler()

    def _asyncio_handler(
        loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        message = context.get("message") or "Unhandled asyncio exception"
        exc = context.get("exception")
        place = context.get("task") or context.get("future") or context.get("handle")
        logger.error(
            "ASYNCIO_UNHANDLED | service=%s | message=%s | where=%r",
            service_name,
            message,
            place,
            exc_info=exc,
        )

        payload = f"service={service_name}; message={message}; where={place!r}"
        if loop.is_closed():
            _enqueue_error_registration_from_any_thread(
                action_type="asyncio_unhandled_exception",
                error=exc or str(context),
                payload=payload,
            )
        else:
            loop.create_task(
                register_runtime_error(
                    action_type="asyncio_unhandled_exception",
                    error=exc or str(context),
                    payload=payload,
                )
            )

        if previous_asyncio_handler is not None:
            try:
                previous_asyncio_handler(loop, context)
            except Exception:
                logger.exception("Previous asyncio exception handler failed")

    _runtime_loop.set_exception_handler(_asyncio_handler)

    import sys

    previous_sys_hook = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_traceback) -> None:
        logger.critical(
            "UNHANDLED_EXCEPTION | service=%s",
            service_name,
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        _enqueue_error_registration_from_any_thread(
            action_type="process_unhandled_exception",
            error=exc_value,
            payload=f"service={service_name}",
        )
        if previous_sys_hook is not None:
            previous_sys_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _sys_hook

    previous_thread_hook = threading.excepthook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "THREAD_UNHANDLED_EXCEPTION | service=%s | thread=%s",
            service_name,
            args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _enqueue_error_registration_from_any_thread(
            action_type="thread_unhandled_exception",
            error=args.exc_value,
            payload=f"service={service_name}; thread={args.thread.name}",
        )
        if previous_thread_hook is not None:
            previous_thread_hook(args)

    threading.excepthook = _thread_hook
