from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession

from client_bot.core.config import GOOGLE_SA_PATH, GOOGLE_SHEET_ID
from client_bot.core.database import async_session
from client_bot.core.metrics import (
    mark_sheets_retry_enqueued,
    mark_sheets_retry_processed,
    mark_sheets_sync,
    set_sheets_retry_queue_size,
)
from client_bot.core.resilience import create_guarded_task, register_runtime_error
from client_bot.domain.models import (
    Order,
    Service,
    ServiceBankDetails,
    ServiceDraft,
    SheetsRetryQueue,
    User,
)
from client_bot.services.sheets_sync import run_full_sync

logger = logging.getLogger(__name__)

_TRACKED_MODELS = (Order, User, Service, ServiceBankDetails, ServiceDraft)

_listener_installed = False
_runtime_loop: asyncio.AbstractEventLoop | None = None
_shutdown_event: asyncio.Event | None = None
_trigger_queue: asyncio.Queue[str] | None = None
_event_worker_task: asyncio.Task[Any] | None = None

_DEBOUNCE_SECONDS = float(os.getenv("SHEETS_SYNC_DEBOUNCE_SECONDS", "2"))
_RETRY_POLL_SECONDS = int(os.getenv("SHEETS_RETRY_POLL_SECONDS", "15"))
_RETRY_BASE_DELAY_SECONDS = int(os.getenv("SHEETS_RETRY_BASE_DELAY_SECONDS", "30"))
_RETRY_MAX_DELAY_SECONDS = int(os.getenv("SHEETS_RETRY_MAX_DELAY_SECONDS", "1800"))
_RETRY_MAX_ATTEMPTS = int(os.getenv("SHEETS_RETRY_MAX_ATTEMPTS", "10"))


class SheetsRetryWorker:
    """Poll and execute delayed sheets retry operations."""

    def __init__(self, *, service_name: str) -> None:
        self._service_name = service_name
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> asyncio.Task[Any]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = create_guarded_task(
            self._run_loop(),
            logger=logger,
            task_name=f"sheets_retry_worker:{self._service_name}",
            action_type="sheets_retry_worker_error",
            payload=f"service={self._service_name}",
        )
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _run_loop(self) -> None:
        logger.info(
            "Sheets retry worker started | service=%s | poll=%ss",
            self._service_name,
            _RETRY_POLL_SECONDS,
        )
        while not self._stop_event.is_set():
            try:
                await process_sheets_retry_queue(limit=50)
            except Exception as exc:
                logger.exception("Sheets retry worker iteration failed")
                await register_runtime_error(
                    action_type="sheets_retry_worker_iteration_error",
                    error=exc,
                    payload=f"service={self._service_name}",
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(_RETRY_POLL_SECONDS, 1),
                )
            except TimeoutError:
                continue


def sheets_enabled() -> bool:
    return bool(GOOGLE_SA_PATH and GOOGLE_SHEET_ID)


def _retry_delay_seconds(attempt: int) -> int:
    delay = _RETRY_BASE_DELAY_SECONDS * (2 ** max(attempt - 1, 0))
    return min(delay, _RETRY_MAX_DELAY_SECONDS)


def _schedule_trigger(models: set[str]) -> None:
    if not models:
        return

    loop = _runtime_loop
    queue = _trigger_queue
    if loop is None or queue is None or not loop.is_running():
        return

    payload = ",".join(sorted(models))

    def _enqueue() -> None:
        assert queue is not None
        try:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)
        except asyncio.QueueEmpty:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("Sheets trigger queue is full, dropping event")

    try:
        loop.call_soon_threadsafe(_enqueue)
    except RuntimeError:
        logger.debug("Failed to schedule sheets sync trigger (loop is closed)")


def _ensure_sqlalchemy_listeners() -> None:
    global _listener_installed
    if _listener_installed:
        return

    session_cls = AsyncSession.sync_session_class

    @sa_event.listens_for(session_cls, "after_flush")
    def _after_flush(session, flush_context) -> None:  # type: ignore[no-untyped-def]
        del flush_context
        changed = session.info.setdefault("_sheets_changed_models", set())
        for obj in session.new.union(session.dirty).union(session.deleted):
            if isinstance(obj, _TRACKED_MODELS):
                changed.add(obj.__class__.__name__)

    @sa_event.listens_for(session_cls, "after_commit")
    def _after_commit(session) -> None:  # type: ignore[no-untyped-def]
        changed = session.info.pop("_sheets_changed_models", None)
        if changed:
            _schedule_trigger(changed)

    @sa_event.listens_for(session_cls, "after_rollback")
    def _after_rollback(session) -> None:  # type: ignore[no-untyped-def]
        session.info.pop("_sheets_changed_models", None)

    _listener_installed = True
    logger.info("Registered SQLAlchemy listeners for event-driven sheets sync")


async def enqueue_sheets_retry(
    *,
    operation: str,
    service_id: int | None = None,
    payload: dict[str, Any] | None = None,
    error: Exception | str | None = None,
) -> None:
    if not sheets_enabled():
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    err_text = str(error)[:2000] if error else None

    async with async_session() as session:
        item = SheetsRetryQueue(
            service_id=service_id,
            operation=operation,
            payload_json=payload_json,
            attempts=0,
            last_attempt_at=None,
            next_retry_at=now,
            last_error=err_text,
        )
        session.add(item)
        await session.commit()

        queue_size = (
            await session.execute(select(func.count(SheetsRetryQueue.id)))
        ).scalar_one()

    mark_sheets_retry_enqueued(operation)
    set_sheets_retry_queue_size(int(queue_size))


async def _execute_retry(item: SheetsRetryQueue) -> bool:
    if item.operation == "sync_full":
        ok = await run_full_sync(source="retry_queue")
        return bool(ok)

    logger.warning("Unknown sheets retry operation dropped: %s", item.operation)
    return True


async def process_sheets_retry_queue(*, limit: int = 25) -> int:
    if not sheets_enabled():
        return 0

    now = datetime.datetime.now(datetime.timezone.utc)

    async with async_session() as session:
        due_items = (
            await session.execute(
                select(SheetsRetryQueue)
                .where(
                    or_(
                        SheetsRetryQueue.next_retry_at.is_(None),
                        SheetsRetryQueue.next_retry_at <= now,
                    )
                )
                .order_by(SheetsRetryQueue.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()

        processed = 0
        for item in due_items:
            processed += 1
            ok = False
            try:
                ok = await _execute_retry(item)
            except Exception as exc:
                item.last_error = str(exc)[:2000]
                ok = False

            if ok:
                mark_sheets_retry_processed(operation=item.operation, result="success")
                await session.delete(item)
                continue

            item.attempts = int(item.attempts or 0) + 1
            item.last_attempt_at = now
            if item.attempts >= _RETRY_MAX_ATTEMPTS:
                mark_sheets_retry_processed(operation=item.operation, result="dropped")
                logger.warning(
                    "Dropping retry item after max attempts | op=%s | id=%s",
                    item.operation,
                    item.id,
                )
                await session.delete(item)
                continue

            item.next_retry_at = now + datetime.timedelta(
                seconds=_retry_delay_seconds(item.attempts)
            )
            mark_sheets_retry_processed(operation=item.operation, result="retry_scheduled")

        await session.commit()

        queue_size = (
            await session.execute(select(func.count(SheetsRetryQueue.id)))
        ).scalar_one()

    set_sheets_retry_queue_size(int(queue_size))
    return processed


async def trigger_sheets_sync(
    *,
    source: str,
    changed_models: set[str] | None = None,
) -> bool:
    if not sheets_enabled():
        return False

    ok = False
    try:
        ok = bool(await run_full_sync(source=source))
    except Exception as exc:
        await register_runtime_error(
            action_type="sheets_sync_trigger_error",
            error=exc,
            payload=f"source={source}",
        )
        ok = False

    if ok:
        mark_sheets_sync(source=source, result="success")
        return True

    mark_sheets_sync(source=source, result="failure")
    await enqueue_sheets_retry(
        operation="sync_full",
        payload={
            "source": source,
            "changed_models": sorted(changed_models or set()),
        },
    )
    return False


async def _event_sync_loop(service_name: str) -> None:
    assert _shutdown_event is not None
    assert _trigger_queue is not None

    logger.info(
        "Event-driven sheets sync started | service=%s | debounce=%ss",
        service_name,
        _DEBOUNCE_SECONDS,
    )

    while not _shutdown_event.is_set():
        try:
            first_payload = await asyncio.wait_for(_trigger_queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        changed_models: set[str] = set()
        changed_models.update(filter(None, first_payload.split(",")))

        while True:
            try:
                payload = await asyncio.wait_for(
                    _trigger_queue.get(),
                    timeout=max(_DEBOUNCE_SECONDS, 0.1),
                )
            except TimeoutError:
                break
            changed_models.update(filter(None, payload.split(",")))

        await trigger_sheets_sync(source=f"event:{service_name}", changed_models=changed_models)


async def start_event_driven_sync(*, service_name: str) -> asyncio.Task[Any] | None:
    """Start SQLAlchemy listeners + debounce worker for commit-driven sheets sync."""
    global _runtime_loop, _shutdown_event, _trigger_queue, _event_worker_task

    if not sheets_enabled():
        logger.info("Sheets disabled, event-driven sync is not started")
        return None

    if _event_worker_task is not None and not _event_worker_task.done():
        return _event_worker_task

    _runtime_loop = asyncio.get_running_loop()
    _shutdown_event = asyncio.Event()
    _trigger_queue = asyncio.Queue(maxsize=500)

    _ensure_sqlalchemy_listeners()

    _event_worker_task = create_guarded_task(
        _event_sync_loop(service_name),
        logger=logger,
        task_name=f"sheets_event_sync:{service_name}",
        action_type="sheets_event_sync_error",
        payload=f"service={service_name}",
    )
    return _event_worker_task


async def stop_event_driven_sync() -> None:
    global _event_worker_task
    if _shutdown_event is not None:
        _shutdown_event.set()

    if _event_worker_task is None:
        return

    if not _event_worker_task.done():
        _event_worker_task.cancel()
    await asyncio.gather(_event_worker_task, return_exceptions=True)
    _event_worker_task = None
