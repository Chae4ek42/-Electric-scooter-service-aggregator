from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import (
    Service,
    ServiceBankDetails,
    ServiceCategory,
    ServiceDraft,
)
from client_bot.services.sheets_sync import (
    sync_bank_details_from_sheet,
    sync_services_from_sheet,
)
from tests._helpers import create_active_owner_bundle, init_db_once, uniq


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


@pytest.mark.asyncio
async def test_sync_services_unknown_specialization_uses_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = await create_active_owner_bundle(service_type="repair")
    service_id = bundle["service_id"]
    owner_user_id = bundle["owner_user_id"]

    rows = [
        {
            "ID": str(service_id),
            "Название": "Сервис после sync",
            "Специализация": "Неизвестный тип",
            "Доступен": "Да",
            "Категория": "Механика",
            "Адрес": "Москва, Sync street, 1",
            "Метро ближ.": "Курская",
        }
    ]

    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)
    monkeypatch.setattr(
        "client_bot.services.sheets_sync._fetch_via_sa", lambda *args, **kwargs: rows
    )

    touched = await sync_services_from_sheet(first_run=False, dry_run=False)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert touched == 1
    assert svc.service_type == "repair"
    assert svc.name == "Сервис после sync"
    assert owner.draft_name == "Сервис после sync"


@pytest.mark.asyncio
async def test_sync_services_invalid_bool_keeps_existing_and_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = await create_active_owner_bundle(service_type="repair")
    service_id = bundle["service_id"]

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        svc.is_available = True
        await session.commit()

    rows = [
        {
            "ID": str(service_id),
            "Название": "Bool fallback test",
            "Специализация": "Ремонт",
            "Доступен": "иногда",
            "Категория": "Механика",
        }
    ]

    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)
    monkeypatch.setattr(
        "client_bot.services.sheets_sync._fetch_via_sa", lambda *args, **kwargs: rows
    )

    touched = await sync_services_from_sheet(first_run=False, dry_run=False)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()

    assert touched == 1
    assert svc.is_available is True


@pytest.mark.asyncio
async def test_sync_services_creates_unknown_category_and_updates_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = await create_active_owner_bundle(service_type="repair")
    service_id = bundle["service_id"]
    owner_user_id = bundle["owner_user_id"]

    new_category = uniq("sheet_cat")
    rows = [
        {
            "ID": str(service_id),
            "Название": "Service with new category",
            "Специализация": "Ремонт",
            "Доступен": "Да",
            "Категория": new_category,
        }
    ]

    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)
    monkeypatch.setattr(
        "client_bot.services.sheets_sync._fetch_via_sa", lambda *args, **kwargs: rows
    )

    await sync_services_from_sheet(first_run=False, dry_run=False)

    async with async_session() as session:
        cat = (
            await session.execute(
                select(ServiceCategory).where(ServiceCategory.name == new_category)
            )
        ).scalar_one_or_none()
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert cat is not None
    assert svc.category_id == cat.id
    assert owner.draft_category == new_category


@pytest.mark.asyncio
async def test_sync_services_invalid_time_fallback_keeps_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = await create_active_owner_bundle(service_type="repair")
    service_id = bundle["service_id"]

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        svc.open_time = "09:00"
        svc.close_time = "20:00"
        await session.commit()

    rows = [
        {
            "ID": str(service_id),
            "Название": "Bad time row",
            "Специализация": "Ремонт",
            "Доступен": "Да",
            "Категория": "Механика",
            "Открытие": "99:99",
            "Закрытие": "oops",
        }
    ]

    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)
    monkeypatch.setattr(
        "client_bot.services.sheets_sync._fetch_via_sa", lambda *args, **kwargs: rows
    )

    await sync_services_from_sheet(first_run=False, dry_run=False)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()

    assert svc.open_time == "09:00"
    assert svc.close_time == "20:00"


@pytest.mark.asyncio
async def test_sync_bank_details_skips_unknown_service_and_updates_owner_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = await create_active_owner_bundle(service_type="repair")
    service_id = bundle["service_id"]
    owner_user_id = bundle["owner_user_id"]

    rows = [
        {
            "ID": "999999999",
            "Форма": "ИП",
            "Налогообложение": "УСН",
            "Расч. счёт": "40702810938000012345",
        },
        {
            "ID": str(service_id),
            "Форма": "ИП",
            "Налогообложение": "УСН",
            "Расч. счёт": "40702810938000012345",
            "Банк": "Тест Банк",
            "БИК": "044525225",
            "Корр. счёт": "30101810400000000225",
            "Организация": "ИП Тест",
            "ИНН": "7707083893",
        },
    ]

    monkeypatch.setattr("client_bot.services.sheets_sync._is_available", lambda: True)
    monkeypatch.setattr(
        "client_bot.services.sheets_sync._fetch_via_sa", lambda *args, **kwargs: rows
    )

    touched = await sync_bank_details_from_sheet(dry_run=False)

    async with async_session() as session:
        bank = (
            await session.execute(
                select(ServiceBankDetails).where(
                    ServiceBankDetails.service_id == service_id
                )
            )
        ).scalar_one_or_none()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert touched == 1
    assert bank is not None
    assert bank.bank_account == "40702810938000012345"
    assert owner.draft_bank_account == "40702810938000012345"
    assert owner.draft_bank_name == "Тест Банк"
