from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import Service, ServiceCategory
from client_bot.services.ranking import RankingContext, rank_services
from tests._helpers import init_db_once, uniq


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


async def _ensure_category(name: str) -> int:
    async with async_session() as session:
        cat = (
            await session.execute(
                select(ServiceCategory).where(ServiceCategory.name == name)
            )
        ).scalar_one_or_none()
        if cat is None:
            cat = ServiceCategory(name=name)
            session.add(cat)
            await session.commit()
            await session.refresh(cat)
        return cat.id


async def _create_service(
    *,
    service_type: str,
    name: str,
    category_name: str | None,
    metro: str = "Арбатская",
    rating: float | None = 4.5,
    open_time: str = "10:00",
    close_time: str = "20:00",
    working_days: str = "Пн,Вт,Ср,Чт,Пт,Сб,Вс",
    has_hydro: bool = False,
    hydro_price: str | None = None,
    upgrade_categories: str | None = None,
) -> int:
    category_id = await _ensure_category(category_name) if category_name else None

    async with async_session() as session:
        svc = Service(
            name=name,
            service_type=service_type,
            category_id=category_id,
            is_available=True,
            registration_complete=True,
            partnership_status="активный",
            address="Москва, Тестовая, 1",
            nearest_metro=metro,
            phone="+79991112233",
            telegram_handle="@rank_test",
            open_time=open_time,
            close_time=close_time,
            working_days=working_days,
            yandex_rating=rating,
            has_hydroisolation=has_hydro,
            hydroisolation_price=hydro_price,
            upgrade_categories=upgrade_categories,
        )
        session.add(svc)
        await session.commit()
        await session.refresh(svc)
        return svc.id


@pytest.mark.asyncio
async def test_rank_services_repair_returns_only_repair_and_complex_for_category() -> (
    None
):
    category = uniq("rank_repair_cat")

    repair_id = await _create_service(
        service_type="repair",
        name=uniq("rank_repair"),
        category_name=category,
        rating=4.9,
    )
    complex_id = await _create_service(
        service_type="complex",
        name=uniq("rank_complex"),
        category_name=category,
        rating=4.2,
        upgrade_categories="Окраска",
    )
    await _create_service(
        service_type="upgrade",
        name=uniq("rank_upgrade"),
        category_name=None,
        rating=4.8,
        upgrade_categories="Окраска",
    )

    ctx = RankingContext(
        service_type="repair",
        malfunction_category=category,
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="12:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session)

    ids = {m.service.id for m in result.matches}
    assert ids == {repair_id, complex_id}
    assert all(m.service.service_type in {"repair", "complex"} for m in result.matches)


@pytest.mark.asyncio
async def test_rank_services_upgrade_hydro_only_returns_hydro_capable() -> None:
    hydro_id = await _create_service(
        service_type="upgrade",
        name=uniq("rank_hydro_yes"),
        category_name=None,
        has_hydro=True,
        hydro_price="1000-2000",
        upgrade_categories="Гидроизоляция,Окраска",
    )
    no_hydro_id = await _create_service(
        service_type="upgrade",
        name=uniq("rank_hydro_no"),
        category_name=None,
        has_hydro=False,
        hydro_price=None,
        upgrade_categories="Окраска",
    )

    ctx = RankingContext(
        service_type="upgrade",
        upgrade_category="Гидроизоляция",
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="12:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session)

    ids = {m.service.id for m in result.matches}
    assert hydro_id in ids
    assert no_hydro_id not in ids
    assert all(m.service.has_hydroisolation is True for m in result.matches)


@pytest.mark.asyncio
async def test_rank_services_returns_time_fallback_when_no_slot_matches() -> None:
    category = uniq("rank_fallback_cat")
    svc_id = await _create_service(
        service_type="repair",
        name=uniq("rank_fallback_service"),
        category_name=category,
        open_time="10:00",
        close_time="18:00",
        rating=4.0,
    )

    ctx = RankingContext(
        service_type="repair",
        malfunction_category=category,
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="23:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session)

    assert result.matches == []
    assert result.time_fallback is True
    assert result.fallback_service_id == svc_id
    assert result.suggested_slots
    assert result.suggested_slots[0][1] == "17:00"


@pytest.mark.asyncio
async def test_rank_services_sorts_by_score_and_rating() -> None:
    category = uniq("rank_order_cat")
    high_id = await _create_service(
        service_type="repair",
        name=uniq("rank_high_rating"),
        category_name=category,
        rating=4.9,
    )
    low_id = await _create_service(
        service_type="repair",
        name=uniq("rank_low_rating"),
        category_name=category,
        rating=4.0,
    )

    ctx = RankingContext(
        service_type="repair",
        malfunction_category=category,
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="12:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session, limit=2)

    assert [m.service.id for m in result.matches] == [high_id, low_id]
    assert result.matches[0].score >= result.matches[1].score


@pytest.mark.asyncio
async def test_rank_services_returns_empty_when_no_matching_upgrade_token() -> None:
    missing_token = uniq("missing_upgrade_token")
    ctx = RankingContext(
        service_type="upgrade",
        upgrade_category=missing_token,
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="12:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session)

    assert result.matches == []
    assert result.time_fallback is False


@pytest.mark.asyncio
async def test_rank_services_upgrade_category_matches_complex_services() -> None:
    token = uniq("upgrade_token")
    complex_id = await _create_service(
        service_type="complex",
        name=uniq("rank_complex_upgrade"),
        category_name=uniq("rank_complex_cat"),
        upgrade_categories=f"{token},Окраска",
    )

    ctx = RankingContext(
        service_type="upgrade",
        upgrade_category=token,
        user_metro="Арбатская",
        scheduled_date="15.11.2030",
        scheduled_time="12:00",
    )

    async with async_session() as session:
        result = await rank_services(ctx, session)

    ids = {m.service.id for m in result.matches}
    assert complex_id in ids
