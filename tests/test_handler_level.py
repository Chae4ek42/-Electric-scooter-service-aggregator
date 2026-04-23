from __future__ import annotations

import os
import sys
import types
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
    status: str,
    service_type: str = "repair",
    upgrade_category: str | None = None,
    hydro_price: str | None = None,
    diagnostics_price: float | None = None,
) -> tuple[int, int, int]:
    async with async_session() as session:
        brand = Brand(name=_uniq("hl_brand"))
        model = Model(name=_uniq("hl_model"), brand=brand)
        category = ServiceCategory(name=_uniq("hl_category"))
        service = Service(
            name=_uniq("hl_service"),
            service_type=service_type,
            category_rel=category,
            is_available=True,
            address="Handler Test Address",
            hydroisolation_price=hydro_price,
            diagnostics_price=diagnostics_price,
        )
        user = User(
            id=_uniq_user_id(),
            username=_uniq("hl_user"),
            full_name="Handler User",
        )
        order = Order(
            user=user,
            service=service,
            model=model,
            status=status,
            metro_station="Арбатская",
            scheduled_date="12.12.2030",
            scheduled_time="12:00",
            upgrade_category=upgrade_category,
            diagnostics_price=diagnostics_price,
        )

        session.add_all([brand, model, category, service, user, order])
        await session.commit()
        return order.id, service.id, user.id


class _FakeMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object | None]] = []
        self.answers: list[tuple[str, object | None]] = []

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data: str, *, user_id: int = 1) -> None:
        self.data = data
        self.from_user = types.SimpleNamespace(id=user_id, username="handler_tester")
        self.message = _FakeMessage()
        self.answer_calls: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answer_calls.append((text, show_alert))


class _FakeState:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self._data = data or {}
        self.cleared = False
        self.state: object | None = None

    async def clear(self) -> None:
        self.cleared = True
        self.state = None

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def set_state(self, state) -> None:
        self.state = state

    async def get_state(self):
        return self.state


@pytest.mark.asyncio
async def test_client_payment_cancel_handler_updates_status_and_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import client_bot.handlers.order as order_handlers

    order_id, _service_id, _user_id = await _make_order(status="awaiting_payment")

    calls: list[str] = []

    async def _fake_safe_edit_or_answer(event, text: str, reply_markup=None) -> None:
        calls.append(text)

    monkeypatch.setattr(
        order_handlers, "_safe_edit_or_answer", _fake_safe_edit_or_answer
    )

    callback = _FakeCallback(f"pay:cancel:{order_id}", user_id=123456)
    state = _FakeState()

    await order_handlers.payment_cancel(callback, state)

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

    assert state.cleared is True
    assert calls == [f"Заявка №{order_id} отменена."]
    assert order.status == "cancelled"
    assert history[-1].to_status == "cancelled"
    assert history[-1].reason == "payment_cancel"


@pytest.mark.asyncio
async def test_partner_accept_order_handler_transitions_paid_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.orders as partner_orders

    order_id, service_id, user_id = await _make_order(status="paid")

    async def _fake_owner(_event):
        return types.SimpleNamespace(service_id=service_id)

    notify_calls: list[tuple[str, int, str, str | None]] = []

    async def _fake_send_by_token(token: str, chat_id: int, text: str, **kwargs):
        notify_calls.append((token, chat_id, text, kwargs.get("dedupe_key")))
        return True

    monkeypatch.setattr(partner_orders, "_require_active_owner", _fake_owner)
    monkeypatch.setattr(partner_orders, "send_by_token", _fake_send_by_token)

    callback = _FakeCallback(f"pord:accept:{order_id}", user_id=777)

    await partner_orders.accept_order(callback, state=None)

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

    assert order.status == "accepted"
    assert order.accepted_at is not None
    assert history[-1].to_status == "accepted"
    assert history[-1].reason == "partner_accept"
    assert len(notify_calls) == 1
    assert notify_calls[0][1] == user_id
    assert notify_calls[0][3] == f"client_accept:{order_id}:{user_id}"


@pytest.mark.asyncio
async def test_partner_accept_order_handler_rejects_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.orders as partner_orders

    order_id, service_id, _user_id = await _make_order(status="completed")

    async def _fake_owner(_event):
        return types.SimpleNamespace(service_id=service_id)

    called = {"notify": 0}

    async def _fake_send_by_token(*args, **kwargs):
        called["notify"] += 1
        return True

    monkeypatch.setattr(partner_orders, "_require_active_owner", _fake_owner)
    monkeypatch.setattr(partner_orders, "send_by_token", _fake_send_by_token)

    callback = _FakeCallback(f"pord:accept:{order_id}", user_id=888)
    await partner_orders.accept_order(callback, state=None)

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

    assert ("Невозможно принять эту заявку.", True) in callback.answer_calls
    assert order.status == "completed"
    assert called["notify"] == 0
    assert all(h.to_status != "accepted" for h in history)


@pytest.mark.asyncio
async def test_partner_estimate_confirm_handler_rejects_invalid_hydro_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.orders as partner_orders

    order_id, service_id, _user_id = await _make_order(
        status="accepted",
        service_type="upgrade",
        upgrade_category="Гидроизоляция",
        hydro_price="1000-2000",
        diagnostics_price=700,
    )

    async def _fake_owner(_event):
        return types.SimpleNamespace(service_id=service_id)

    called = {"notify": 0}

    async def _fake_send_by_token(*args, **kwargs):
        called["notify"] += 1
        return True

    monkeypatch.setattr(partner_orders, "_require_active_owner", _fake_owner)
    monkeypatch.setattr(partner_orders, "send_by_token", _fake_send_by_token)

    state = _FakeState(
        {
            "estimate_order_id": order_id,
            "est_cost": 900,
            "est_items": "Работы",
            "est_deadline": "2 дня",
            "est_description": "Тест",
        }
    )
    callback = _FakeCallback("pord:estimate_confirm", user_id=999)

    await partner_orders.estimate_confirm(callback, state)

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

    assert (
        "Цена должна быть в диапазоне 1000-2000 руб.",
        True,
    ) in callback.answer_calls
    assert state.cleared is False
    assert order.status == "accepted"
    assert called["notify"] == 0
    assert all(h.to_status != "in_progress" for h in history)


@pytest.mark.asyncio
async def test_registration_complex_category_moves_to_upgrade_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as registration_handlers
    from client_bot.domain.models import ServiceDraft
    from client_bot.domain.states import RegistrationFSM

    async def _fake_update_draft(*args, **kwargs):
        return None

    async def _fake_get_owner(_tg_id: int):
        return ServiceDraft(
            owner_user_id=123,
            status="ожидает",
            draft_service_type="complex",
            draft_upgrade_categories="Окраска",
        )

    async def _fake_after_edit(_event, _state):
        return False

    captured = {"text": None}

    async def _fake_safe_edit_or_answer(_event, text: str, reply_markup=None):
        captured["text"] = text

    monkeypatch.setattr(registration_handlers, "_update_draft", _fake_update_draft)
    monkeypatch.setattr(registration_handlers, "_get_owner", _fake_get_owner)
    monkeypatch.setattr(registration_handlers, "_after_edit", _fake_after_edit)
    monkeypatch.setattr(
        registration_handlers,
        "_safe_edit_or_answer",
        _fake_safe_edit_or_answer,
    )

    callback = _FakeCallback("reg_cat:Механика", user_id=123)
    state = _FakeState()

    await registration_handlers.reg_category(callback, state)

    assert state.state == RegistrationFSM.reg_upgrade_categories
    assert captured["text"] == "Выберите категории апгрейда:"


@pytest.mark.asyncio
async def test_registration_rejects_legacy_bank_edit_callback() -> None:
    import partner_bot.handlers.registration as registration_handlers

    callback = _FakeCallback("edit_draft:bank", user_id=321)
    state = _FakeState()

    await registration_handlers.edit_draft_field(callback, state)

    assert callback.answer_calls == [("Неизвестное поле", False)]
    assert state.state is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_order_time_fallback_uses_suggested_date_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import client_bot.handlers.order as order_handlers
    from client_bot.services.ranking import RankingResult

    async def _fake_rank_services(ctx, session, limit=1):
        return RankingResult(
            matches=[],
            time_fallback=True,
            suggested_date="13.11.2030",
            suggested_time="10:00",
        )

    captured: dict[str, object] = {}

    async def _fake_safe_edit_or_answer(_event, text: str, reply_markup=None):
        captured["text"] = text
        captured["reply_markup"] = reply_markup

    monkeypatch.setattr(order_handlers, "rank_services", _fake_rank_services)
    monkeypatch.setattr(
        order_handlers,
        "_safe_edit_or_answer",
        _fake_safe_edit_or_answer,
    )

    callback = _FakeCallback("time:09:00", user_id=404)
    state = _FakeState(
        {
            "service_type": "repair",
            "scheduled_date": "12.11.2030",
            "metro_station": "Курская",
        }
    )

    await order_handlers.pick_time(callback, state)

    assert "Ближайший доступный слот" in captured["text"]
    assert "13.11.2030 10:00" in captured["text"]

    kb = captured["reply_markup"]
    first_button = kb.inline_keyboard[0][0]
    assert first_button.callback_data == "time_suggest:13.11.2030:10:00"


@pytest.mark.asyncio
async def test_order_pick_suggested_time_parses_date_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import client_bot.handlers.order as order_handlers

    captured: dict[str, object] = {}

    async def _fake_process_time_choice(callback, state, time_str: str):
        captured["time_str"] = time_str
        captured["state_data"] = await state.get_data()

    monkeypatch.setattr(
        order_handlers,
        "_process_time_choice",
        _fake_process_time_choice,
    )

    callback = _FakeCallback("time_suggest:15.11.2030:14:00", user_id=505)
    state = _FakeState({"service_type": "repair"})

    await order_handlers.pick_suggested_time(callback, state)

    assert captured["time_str"] == "14:00"
    assert captured["state_data"]["scheduled_date"] == "15.11.2030"
