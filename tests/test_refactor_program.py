from __future__ import annotations

import asyncio
import datetime
import json
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
from client_bot.services import notifications
from client_bot.services.order_lifecycle import (
    ACTOR_CLIENT,
    ACTOR_SYSTEM,
    cancel_expired_awaiting_payment,
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
    created_at: datetime.datetime | None = None,
    upgrade_category: str | None = None,
    diagnostics_price: float | None = None,
) -> int:
    async with async_session() as session:
        brand = Brand(name=_uniq("brand"))
        model = Model(name=_uniq("model"), brand=brand)
        category = ServiceCategory(name=_uniq("category"))
        service = Service(
            name=_uniq("service"),
            service_type="repair",
            category_rel=category,
            is_available=True,
            address="Test Address",
            diagnostics_price=diagnostics_price,
        )
        user = User(id=_uniq_user_id(), username=_uniq("user"), full_name="Test User")
        order = Order(
            user=user,
            service=service,
            model=model,
            status=status,
            metro_station="Арбатская",
            scheduled_date="10.10.2030",
            scheduled_time="10:00",
            upgrade_category=upgrade_category,
            diagnostics_price=diagnostics_price,
            created_at=created_at or datetime.datetime.now(datetime.timezone.utc),
        )

        session.add_all([brand, model, category, service, user, order])
        await session.commit()
        return order.id


@pytest.mark.asyncio
async def test_transition_order_status_creates_history() -> None:
    order_id = await _make_order(status="awaiting_payment")

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        changed = await transition_order_status(
            session,
            order,
            "paid",
            actor=ACTOR_CLIENT,
            reason="prepayment_received",
            metadata={"source": "test"},
        )
        assert changed is True
        await session.commit()

    async with async_session() as session:
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
        assert len(history_rows) == 1
        assert history_rows[0].from_status == "awaiting_payment"
        assert history_rows[0].to_status == "paid"
        assert history_rows[0].actor == ACTOR_CLIENT
        assert history_rows[0].reason == "prepayment_received"
        assert json.loads(history_rows[0].metadata_json) == {"source": "test"}


@pytest.mark.asyncio
async def test_transition_order_status_idempotent_same_status() -> None:
    order_id = await _make_order(status="awaiting_payment")

    async with async_session() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
        changed = await transition_order_status(
            session,
            order,
            "awaiting_payment",
            actor=ACTOR_SYSTEM,
            reason="noop",
        )
        assert changed is False
        await session.commit()

    async with async_session() as session:
        history_rows = (
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
        assert history_rows == []


@pytest.mark.asyncio
async def test_cancel_expired_awaiting_payment_marks_old_orders() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    old_order_id = await _make_order(
        status="awaiting_payment",
        created_at=now - datetime.timedelta(hours=2),
    )
    fresh_order_id = await _make_order(
        status="awaiting_payment",
        created_at=now - datetime.timedelta(minutes=10),
    )

    async with async_session() as session:
        changed = await cancel_expired_awaiting_payment(
            session,
            now - datetime.timedelta(hours=1),
        )
        assert changed == 1
        await session.commit()

    async with async_session() as session:
        old_order = (
            await session.execute(select(Order).where(Order.id == old_order_id))
        ).scalar_one()
        fresh_order = (
            await session.execute(select(Order).where(Order.id == fresh_order_id))
        ).scalar_one()
        assert old_order.status == "cancelled"
        assert fresh_order.status == "awaiting_payment"

        old_history = (
            (
                await session.execute(
                    select(OrderStatusHistory)
                    .where(OrderStatusHistory.order_id == old_order_id)
                    .order_by(OrderStatusHistory.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(old_history) == 1
        assert old_history[0].reason == "payment_timeout"


class _FakeBot:
    def __init__(self, fail_attempts: int = 0) -> None:
        self.fail_attempts = fail_attempts
        self.calls = 0

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.calls += 1
        if self.fail_attempts > 0:
            self.fail_attempts -= 1
            raise RuntimeError("temporary send error")


@pytest.mark.asyncio
async def test_notifications_retry_after_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications._dedupe_cache.clear()

    async def _fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    bot = _FakeBot(fail_attempts=1)
    sent = await notifications.send_with_retry(
        bot,
        12345,
        "retry test",
        dedupe_key=f"retry-{uuid.uuid4().hex}",
        max_attempts=3,
        base_delay_sec=0.001,
    )

    assert sent is True
    assert bot.calls == 2


@pytest.mark.asyncio
async def test_notifications_dedupe_key_blocks_duplicates() -> None:
    notifications._dedupe_cache.clear()
    bot = _FakeBot()
    dedupe_key = f"dedupe-{uuid.uuid4().hex}"

    first = await notifications.send_with_retry(bot, 1, "hello", dedupe_key=dedupe_key)
    second = await notifications.send_with_retry(bot, 1, "hello", dedupe_key=dedupe_key)

    assert first is True
    assert second is False
    assert bot.calls == 1


def test_payment_policy_hydro_and_remainder() -> None:
    prepayment = PaymentPolicy.prepayment("Гидроизоляция", diagnostics_price=1200)
    remainder = PaymentPolicy.remainder(1400, "Гидроизоляция", diagnostics_price=1200)

    assert prepayment == PaymentPolicy.total(500)
    assert remainder == PaymentPolicy.total(900)

    valid, rng = PaymentPolicy.validate_hydro_total(1500, "1000-2000")
    assert valid is True
    assert rng is not None
    assert rng[0] == PaymentPolicy.total(1000)
    assert rng[1] == PaymentPolicy.total(2000)

    invalid, _ = PaymentPolicy.validate_hydro_total(900, "1000-2000")
    assert invalid is False

    # Remainder can never be negative.
    assert PaymentPolicy.remainder(
        200, None, diagnostics_price=500
    ) == PaymentPolicy.total(0)


@pytest.mark.asyncio
async def test_run_full_sync_dry_run_skips_outbound_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import client_bot.services.sheets_sync as sheets_sync
    import client_bot.services.sheets_writer as sheets_writer

    calls = {
        "services": 0,
        "bank": 0,
        "orders": 0,
        "clients": 0,
    }

    async def _fake_services(*, first_run: bool = False, dry_run: bool = False) -> int:
        assert first_run is True
        assert dry_run is True
        calls["services"] += 1
        return 2

    async def _fake_bank(*, dry_run: bool = False) -> int:
        assert dry_run is True
        calls["bank"] += 1
        return 1

    def _fake_orders() -> None:
        calls["orders"] += 1

    def _fake_clients() -> None:
        calls["clients"] += 1

    monkeypatch.setattr(sheets_sync, "sync_services_from_sheet", _fake_services)
    monkeypatch.setattr(sheets_sync, "sync_bank_details_from_sheet", _fake_bank)
    monkeypatch.setattr(sheets_writer, "sync_all_orders_to_sheet", _fake_orders)
    monkeypatch.setattr(sheets_writer, "sync_all_clients_to_sheet", _fake_clients)

    await sheets_sync.run_full_sync(first_run=True, dry_run=True)

    assert calls["services"] == 1
    assert calls["bank"] == 1
    assert calls["orders"] == 0
    assert calls["clients"] == 0


@pytest.mark.asyncio
async def test_run_full_sync_regular_mode_runs_outbound_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import client_bot.services.sheets_sync as sheets_sync
    import client_bot.services.sheets_writer as sheets_writer

    calls = {
        "services": 0,
        "bank": 0,
        "orders": 0,
        "clients": 0,
    }

    async def _fake_services(*, first_run: bool = False, dry_run: bool = False) -> int:
        assert dry_run is False
        calls["services"] += 1
        return 1

    async def _fake_bank(*, dry_run: bool = False) -> int:
        assert dry_run is False
        calls["bank"] += 1
        return 1

    def _fake_orders() -> None:
        calls["orders"] += 1

    def _fake_clients() -> None:
        calls["clients"] += 1

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(sheets_sync, "sync_services_from_sheet", _fake_services)
    monkeypatch.setattr(sheets_sync, "sync_bank_details_from_sheet", _fake_bank)
    monkeypatch.setattr(sheets_writer, "sync_all_orders_to_sheet", _fake_orders)
    monkeypatch.setattr(sheets_writer, "sync_all_clients_to_sheet", _fake_clients)
    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)

    await sheets_sync.run_full_sync(first_run=False, dry_run=False)

    assert calls["services"] == 1
    assert calls["bank"] == 1
    assert calls["orders"] == 1
    assert calls["clients"] == 1
