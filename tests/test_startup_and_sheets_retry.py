from __future__ import annotations

import logging

import pytest

from client_bot.core.startup import run_with_retry
from client_bot.services import sheets_events


@pytest.mark.asyncio
async def test_run_with_retry_recovers_after_temporary_failure() -> None:
    attempts = {"count": 0}

    async def _operation() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary network failure")

    ok = await run_with_retry(
        action_name="set_my_commands",
        operation=_operation,
        logger=logging.getLogger(__name__),
        attempts=5,
        base_delay=0.01,
        max_delay=0.05,
    )

    assert ok is True
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_run_with_retry_stops_after_max_attempts() -> None:
    attempts = {"count": 0}

    async def _operation() -> None:
        attempts["count"] += 1
        raise RuntimeError("telegram api unavailable")

    ok = await run_with_retry(
        action_name="set_my_commands",
        operation=_operation,
        logger=logging.getLogger(__name__),
        attempts=3,
        base_delay=0.01,
        max_delay=0.05,
    )

    assert ok is False
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_trigger_sheets_sync_enqueues_retry_on_failure(monkeypatch) -> None:
    enqueued: dict[str, object] = {}

    async def _run_full_sync(*, source: str = "manual", **kwargs) -> bool:
        del source, kwargs
        return False

    async def _enqueue(**kwargs) -> None:
        enqueued.update(kwargs)

    monkeypatch.setattr(sheets_events, "sheets_enabled", lambda: True)
    monkeypatch.setattr(sheets_events, "run_full_sync", _run_full_sync)
    monkeypatch.setattr(sheets_events, "enqueue_sheets_retry", _enqueue)

    ok = await sheets_events.trigger_sheets_sync(
        source="test",
        changed_models={"Order", "Service"},
    )

    assert ok is False
    assert enqueued.get("operation") == "sync_full"
    payload = enqueued.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("source") == "test"


@pytest.mark.asyncio
async def test_trigger_sheets_sync_success_does_not_enqueue(monkeypatch) -> None:
    called = {"enqueue": 0}

    async def _run_full_sync(*, source: str = "manual", **kwargs) -> bool:
        del source, kwargs
        return True

    async def _enqueue(**kwargs) -> None:
        del kwargs
        called["enqueue"] += 1

    monkeypatch.setattr(sheets_events, "sheets_enabled", lambda: True)
    monkeypatch.setattr(sheets_events, "run_full_sync", _run_full_sync)
    monkeypatch.setattr(sheets_events, "enqueue_sheets_retry", _enqueue)

    ok = await sheets_events.trigger_sheets_sync(source="test")

    assert ok is True
    assert called["enqueue"] == 0
