from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.services.sheets_sync import (
    sync_bank_details_from_sheet,
    sync_services_from_sheet,
)
from tests._helpers import init_db_once


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


@pytest.mark.asyncio
async def test_sync_services_from_sheet_disabled_in_write_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)

    def _must_not_read(*args, **kwargs):
        raise AssertionError("_fetch_via_sa must not be called in write-only mode")

    monkeypatch.setattr("client_bot.services.sheets_sync._fetch_via_sa", _must_not_read)

    touched = await sync_services_from_sheet(first_run=True, dry_run=False)
    assert touched == 0


@pytest.mark.asyncio
async def test_sync_bank_details_from_sheet_disabled_in_write_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)

    def _must_not_read(*args, **kwargs):
        raise AssertionError("_fetch_via_sa must not be called in write-only mode")

    monkeypatch.setattr("client_bot.services.sheets_sync._fetch_via_sa", _must_not_read)

    touched = await sync_bank_details_from_sheet(dry_run=False)
    assert touched == 0
