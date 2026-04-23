from __future__ import annotations

import datetime
import os
import sys
import uuid

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import (
    Brand,
    Model,
    Order,
    OrderStatusHistory,
    Service,
    ServiceCategory,
    User,
)
from client_bot.services.order_lifecycle import (
    ACTOR_CLIENT,
    ACTOR_PARTNER,
    transition_order_status,
)
from client_bot.services.payment_policy import PaymentPolicy


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _uniq_user_id() -> int:
    return int(uuid.uuid4().int % 9_000_000_000_000) + 1_000_000_000_000


@pytest.fixture(scope="module", autouse=True)
async def _init_db_for_module() -> None:
    from client_bot.services.seed import init_db

    await init_db()


async def _make_order(
    *,
    status: str = "awaiting_payment",
    upgrade_category: str | None = None,
    diagnostics_price: float | None = None,
) -> int:
    async with async_session() as session:
        brand = Brand(name=_uniq("sc_brand"))
        model = Model(name=_uniq("sc_model"), brand=brand)
        category = ServiceCategory(name=_uniq("sc_category"))
        service = Service(
            name=_uniq("sc_service"),
            service_type="repair",
            category_rel=category,
            is_available=True,
            address="Scenario Address",
            diagnostics_price=diagnostics_price,
            hydroisolation_price="1000-2000",
        )
        user = User(
            id=_uniq_user_id(), username=_uniq("sc_user"), full_name="Scenario User"
        )
        order = Order(
            user=user,
            service=service,
            model=model,
            status=status,
            metro_station="Тверская",
            scheduled_date="11.11.2030",
            scheduled_time="11:00",
            upgrade_category=upgrade_category,
            diagnostics_price=diagnostics_price,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )

        session.add_all([brand, model, category, service, user, order])
        await session.commit()
        return order.id


@pytest.mark.asyncio
async def test_user_scenario_full_mock_payment_lifecycle() -> None:
    order_id = await _make_order(status="awaiting_payment", diagnostics_price=300)

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        order.total_cost = float(PaymentPolicy.total(2300))

        await transition_order_status(
            session,
            order,
            "paid",
            actor=ACTOR_CLIENT,
            reason="prepayment_mock_paid",
        )
        await transition_order_status(
            session,
            order,
            "accepted",
            actor=ACTOR_PARTNER,
            reason="partner_accept",
        )
        await transition_order_status(
            session,
            order,
            "in_progress",
            actor=ACTOR_PARTNER,
            reason="estimate_confirmed",
        )
        await transition_order_status(
            session,
            order,
            "ready_for_pickup",
            actor=ACTOR_PARTNER,
            reason="partner_ready_for_pickup",
        )
        await transition_order_status(
            session,
            order,
            "completed",
            actor=f"{ACTOR_CLIENT}:mock_final_payment",
            reason="final_mock_payment",
        )
        await session.commit()

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        history_rows = (
            (
                await session.execute(
                    select(OrderStatusHistory)
                    .where(OrderStatusHistory.order_id == order_id)
                    .order_by(OrderStatusHistory.id)
                )
            )
            .scalars()
            .all()
        )

        assert order.status == "completed"
        assert [h.to_status for h in history_rows] == [
            "paid",
            "accepted",
            "in_progress",
            "ready_for_pickup",
            "completed",
        ]

        total = PaymentPolicy.total(order.total_cost)
        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category, order.diagnostics_price
        )
        remainder = PaymentPolicy.remainder(
            total,
            order.upgrade_category,
            order.diagnostics_price,
        )

        assert prepayment == PaymentPolicy.total(300)
        assert remainder == PaymentPolicy.total(2000)


@pytest.mark.asyncio
async def test_user_scenario_hydro_pricing_and_progress_flow() -> None:
    order_id = await _make_order(
        status="awaiting_payment",
        upgrade_category="Гидроизоляция",
        diagnostics_price=900,
    )

    # Scenario validation before partner confirms estimate.
    bad_total_ok, _ = PaymentPolicy.validate_hydro_total(900, "1000-2000")
    good_total_ok, _ = PaymentPolicy.validate_hydro_total(1500, "1000-2000")
    assert bad_total_ok is False
    assert good_total_ok is True

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        order.total_cost = float(PaymentPolicy.total(1500))

        await transition_order_status(
            session,
            order,
            "accepted",
            actor=ACTOR_PARTNER,
            reason="partner_accept",
        )
        await transition_order_status(
            session,
            order,
            "in_progress",
            actor=ACTOR_PARTNER,
            reason="estimate_confirmed",
        )
        await session.commit()

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        history_rows = (
            (
                await session.execute(
                    select(OrderStatusHistory)
                    .where(OrderStatusHistory.order_id == order_id)
                    .order_by(OrderStatusHistory.id)
                )
            )
            .scalars()
            .all()
        )

        prepayment = PaymentPolicy.prepayment(
            order.upgrade_category, order.diagnostics_price
        )
        remainder = PaymentPolicy.remainder(
            PaymentPolicy.total(order.total_cost),
            order.upgrade_category,
            order.diagnostics_price,
        )

        assert order.status == "in_progress"
        assert prepayment == PaymentPolicy.total(500)
        assert remainder == PaymentPolicy.total(1000)
        assert [h.to_status for h in history_rows] == ["accepted", "in_progress"]
