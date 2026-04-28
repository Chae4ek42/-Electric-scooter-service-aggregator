from __future__ import annotations

import os
import sys
import types

import pytest
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import Service, ServiceDraft
from client_bot.domain.states import RegistrationFSM
from tests._helpers import create_active_owner_bundle, init_db_once


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {}
        self.state = None
        self.cleared = False

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self._data)

    async def clear(self) -> None:
        self._data.clear()
        self.state = None
        self.cleared = True


class FakeMessage:
    def __init__(self, *, user_id: int, text: str, username: str = "tester") -> None:
        self.from_user = types.SimpleNamespace(
            id=user_id,
            username=username,
            full_name="Test User",
        )
        self.text = text
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))


class FakeCallbackMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, object | None]] = []
        self.edits: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str, *, user_id: int) -> None:
        self.data = data
        self.from_user = types.SimpleNamespace(
            id=user_id,
            username="tester",
            full_name="Test User",
        )
        self.message = FakeCallbackMessage()
        self.answer_calls: list[tuple[str | None, bool]] = []
        self.bot = types.SimpleNamespace(send_message=_noop_send)

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answer_calls.append((text, show_alert))


async def _noop_send(*args, **kwargs):
    return None


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


@pytest.mark.asyncio
async def test_reg_hydro_yes_requires_hydro_price_even_in_edit_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as reg

    async def _fake_update_draft(*args, **kwargs):
        return None

    async def _fake_safe_edit_or_answer(_event, text: str, reply_markup=None):
        _fake_safe_edit_or_answer.last_text = text

    _fake_safe_edit_or_answer.last_text = ""

    async def _fake_after_edit(_event, _state):
        return False

    monkeypatch.setattr(reg, "_update_draft", _fake_update_draft)
    monkeypatch.setattr(reg, "_safe_edit_or_answer", _fake_safe_edit_or_answer)
    monkeypatch.setattr(reg, "_after_edit", _fake_after_edit)

    callback = FakeCallback("reg_hydro:yes", user_id=123)
    state = FakeState({"editing_draft": True, "editing_draft_field": "hydro"})

    await reg.reg_hydro(callback, state)

    assert state.state == RegistrationFSM.reg_hydro_price
    assert "стоимость гидроизоляции" in _fake_safe_edit_or_answer.last_text.lower()


@pytest.mark.asyncio
async def test_reg_diagnostics_chains_to_diag_included(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as reg

    async def _fake_menu_interrupt(_message, _state):
        return False

    async def _fake_update_draft(*args, **kwargs):
        return None

    monkeypatch.setattr(reg, "_handle_menu_interrupt", _fake_menu_interrupt)
    monkeypatch.setattr(reg, "_update_draft", _fake_update_draft)

    message = FakeMessage(user_id=101, text="700")
    state = FakeState({"editing_draft": True, "editing_draft_field": "diagnostics"})

    await reg.reg_diagnostics(message, state)

    assert state.state == RegistrationFSM.reg_diag_included
    assert message.answers
    assert "входит в стоимость" in message.answers[-1][0].lower()


@pytest.mark.asyncio
async def test_after_edit_active_profile_syncs_service_and_notifies_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as reg

    owner = types.SimpleNamespace(
        owner_user_id=777,
        status="активный",
        draft_service_type="repair",
    )
    updated_service = types.SimpleNamespace(id=55)

    calls = {
        "sheet": 0,
        "notify": 0,
        "safe": 0,
    }

    async def _fake_get_owner(_tg_id: int):
        return owner

    async def _fake_sync(_owner_user_id: int):
        return updated_service

    def _fake_schedule(_svc):
        calls["sheet"] += 1

    async def _fake_notify(_event, *, service_id: int, field_key: str | None):
        assert service_id == 55
        assert field_key == "hydro"
        calls["notify"] += 1

    async def _fake_safe(_event, text: str, reply_markup=None):
        assert "Выберите поле для изменения" in text
        calls["safe"] += 1

    monkeypatch.setattr(reg, "_get_owner", _fake_get_owner)
    monkeypatch.setattr(reg, "_sync_active_service_from_draft", _fake_sync)
    monkeypatch.setattr(reg, "_schedule_service_sheet_sync", _fake_schedule)
    monkeypatch.setattr(reg, "_notify_admins_about_active_profile_edit", _fake_notify)
    monkeypatch.setattr(reg, "_safe_edit_or_answer", _fake_safe)
    monkeypatch.setattr(reg, "_format_draft", lambda _owner: "DRAFT")

    callback = FakeCallback("irrelevant", user_id=777)
    state = FakeState({"editing_draft": True, "editing_draft_field": "hydro"})

    handled = await reg._after_edit(callback, state)

    assert handled is True
    assert state.cleared is True
    assert calls["sheet"] == 1
    assert calls["notify"] == 1
    assert calls["safe"] == 1


@pytest.mark.asyncio
async def test_profile_edit_profile_enters_draft_edit_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.profile as profile

    owner = types.SimpleNamespace(owner_user_id=1001, draft_service_type="repair")
    svc = types.SimpleNamespace(id=77)

    async def _fake_require_active(_message):
        return owner, svc

    async def _fake_sync_owner_draft(_owner_user_id: int, _service_id: int):
        return None

    async def _fake_get_owner(_tg_id: int):
        return owner

    monkeypatch.setattr(profile, "_require_active", _fake_require_active)
    monkeypatch.setattr(
        profile, "_sync_owner_draft_from_service", _fake_sync_owner_draft
    )
    monkeypatch.setattr(profile, "_get_owner", _fake_get_owner)
    monkeypatch.setattr(profile, "_format_draft", lambda _owner: "DRAFT")

    state = FakeState()
    message = FakeMessage(user_id=1001, text="Редактировать профиль")

    await profile.edit_profile(message, state)

    assert message.answers
    text, markup = message.answers[-1]
    assert "Выберите поле для изменения" in text
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Тип услуг" in labels
    assert "Telegram" not in labels


@pytest.mark.asyncio
async def test_profile_edit_metro_uses_station_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.profile as profile

    bundle = await create_active_owner_bundle(service_type="repair")
    owner_user_id = bundle["owner_user_id"]
    service_id = bundle["service_id"]

    async def _fake_notify(*args, **kwargs):
        return None

    def _fake_schedule(*args, **kwargs):
        return None

    monkeypatch.setattr(profile, "_notify_admins_about_profile_update", _fake_notify)
    monkeypatch.setattr(profile, "_schedule_sheets_call", _fake_schedule)

    state = FakeState({"edit_field": "metro", "edit_service_id": service_id})
    message = FakeMessage(user_id=owner_user_id, text="курская")

    await profile.accept_field_value(message, state)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert svc.nearest_metro == "Курская"
    assert owner.draft_metro == "Курская"
    assert owner.status == "активный"
    assert message.answers
    assert "Поле обновлено." in message.answers[-1][0]


@pytest.mark.asyncio
async def test_profile_edit_hydro_price_keeps_active_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.profile as profile

    bundle = await create_active_owner_bundle(service_type="repair")
    owner_user_id = bundle["owner_user_id"]
    service_id = bundle["service_id"]

    async def _fake_notify(*args, **kwargs):
        return None

    def _fake_schedule(*args, **kwargs):
        return None

    monkeypatch.setattr(profile, "_notify_admins_about_profile_update", _fake_notify)
    monkeypatch.setattr(profile, "_schedule_sheets_call", _fake_schedule)

    state = FakeState({"edit_field": "hydro_price", "edit_service_id": service_id})
    message = FakeMessage(user_id=owner_user_id, text="1000-1500")

    await profile.accept_field_value(message, state)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert svc.has_hydroisolation is True
    assert svc.hydroisolation_price == "1000-1500"
    assert owner.status == "активный"
    assert svc.partnership_status == "активный"
