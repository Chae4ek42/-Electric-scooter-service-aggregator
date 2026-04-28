from __future__ import annotations

import datetime
import os
import sys

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import Order, OrderStatusHistory
from client_bot.services.order_lifecycle import (
    ACTOR_CLIENT,
    ACTOR_PARTNER,
    cancel_expired_awaiting_payment,
    transition_order_status,
    transition_order_status_by_id,
)
from client_bot.services.payment_policy import PaymentPolicy
from tests._helpers import create_order_bundle, init_db_once


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


@pytest.mark.asyncio
async def test_full_paid_order_lifecycle_records_history() -> None:
    bundle = await create_order_bundle(status="awaiting_payment", diagnostics_price=300)
    order_id = bundle["order_id"]

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
            reason="repair_done",
        )
        await transition_order_status(
            session,
            order,
            "completed",
            actor=f"{ACTOR_CLIENT}:mock_final_payment",
            reason="final_payment",
        )
        await session.commit()

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        history = (
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
    assert [h.to_status for h in history] == [
        "paid",
        "accepted",
        "in_progress",
        "ready_for_pickup",
        "completed",
    ]


@pytest.mark.asyncio
async def test_transition_idempotent_true_does_not_duplicate_history() -> None:
    bundle = await create_order_bundle(status="accepted")
    order_id = bundle["order_id"]

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()

        changed = await transition_order_status(
            session,
            order,
            "accepted",
            actor=ACTOR_PARTNER,
            reason="duplicate_noop",
            idempotent=True,
        )
        await session.commit()

    async with async_session() as session:
        history_count = (
            (
                await session.execute(
                    select(OrderStatusHistory).where(
                        OrderStatusHistory.order_id == order_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert changed is False
    assert history_count == []


@pytest.mark.asyncio
async def test_transition_idempotent_false_allows_same_status_history_row() -> None:
    bundle = await create_order_bundle(status="accepted")
    order_id = bundle["order_id"]

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()

        changed = await transition_order_status(
            session,
            order,
            "accepted",
            actor=ACTOR_PARTNER,
            reason="manual_reconfirm",
            metadata={"source": "test"},
            idempotent=False,
        )
        await session.commit()

    async with async_session() as session:
        history = (
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

    assert changed is True
    assert len(history) == 1
    assert history[0].from_status == "accepted"
    assert history[0].to_status == "accepted"


@pytest.mark.asyncio
async def test_invalid_status_transition_raises_value_error() -> None:
    bundle = await create_order_bundle(status="awaiting_payment")
    order_id = bundle["order_id"]

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        with pytest.raises(ValueError):
            await transition_order_status(
                session,
                order,
                "completed",
                actor=ACTOR_PARTNER,
                reason="invalid_jump",
            )


@pytest.mark.asyncio
async def test_transition_order_status_by_id_returns_false_for_missing_order() -> None:
    async with async_session() as session:
        changed = await transition_order_status_by_id(
            session,
            999999999,
            "cancelled",
            actor=ACTOR_CLIENT,
            reason="missing_order",
        )

    assert changed is False


@pytest.mark.asyncio
async def test_cancel_expired_awaiting_payment_changes_only_old_orders() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    old_bundle = await create_order_bundle(
        status="awaiting_payment",
        created_at=now - datetime.timedelta(days=3),
    )
    fresh_bundle = await create_order_bundle(
        status="awaiting_payment",
        created_at=now - datetime.timedelta(minutes=20),
    )

    async with async_session() as session:
        changed = await cancel_expired_awaiting_payment(
            session,
            cutoff=now - datetime.timedelta(hours=1),
        )
        await session.commit()

    async with async_session() as session:
        old_order = (
            await session.execute(
                select(Order).where(Order.id == old_bundle["order_id"])
            )
        ).scalar_one()
        fresh_order = (
            await session.execute(
                select(Order).where(Order.id == fresh_bundle["order_id"])
            )
        ).scalar_one()

    assert changed == 1
    assert old_order.status == "cancelled"
    assert fresh_order.status == "awaiting_payment"


@pytest.mark.asyncio
async def test_payment_policy_hydro_scenario_calculates_expected_prepayment() -> None:
    total = PaymentPolicy.total(1500)
    prepayment = PaymentPolicy.prepayment("Гидроизоляция", diagnostics_price=900)
    remainder = PaymentPolicy.remainder(total, "Гидроизоляция", diagnostics_price=900)

    assert prepayment == PaymentPolicy.total(500)
    assert remainder == PaymentPolicy.total(1000)


@pytest.mark.asyncio
async def test_payment_policy_hydro_range_validation() -> None:
    bad_total_ok, bad_range = PaymentPolicy.validate_hydro_total(900, "1000-2000")
    good_total_ok, good_range = PaymentPolicy.validate_hydro_total(1500, "1000-2000")

    assert bad_total_ok is False
    assert good_total_ok is True
    assert bad_range == good_range
