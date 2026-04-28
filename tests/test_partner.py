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

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self._data)


class FakeCallbackMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str, *, user_id: int) -> None:
        self.data = data
        self.from_user = types.SimpleNamespace(
            id=user_id,
            username="partner_tester",
            full_name="Partner Tester",
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


def test_profile_edit_keyboard_hides_telegram() -> None:
    from partner_bot.ui.keyboards import profile_edit_fields_kb

    kb = profile_edit_fields_kb()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]

    assert "Telegram" not in labels
    assert "Название" in labels
    assert "Метро" in labels


def test_registration_confirm_keyboard_has_back_button() -> None:
    from partner_bot.ui.keyboards import reg_confirm_kb

    kb = reg_confirm_kb(has_bank=False)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]

    assert "Назад" in labels
    assert "Отправить" in labels


def test_draft_edit_keyboard_has_type_and_category_controls() -> None:
    from partner_bot.ui.keyboards import draft_edit_kb

    kb_repair = draft_edit_kb("repair")
    repair_labels = [btn.text for row in kb_repair.inline_keyboard for btn in row]

    kb_upgrade = draft_edit_kb("upgrade")
    upgrade_labels = [btn.text for row in kb_upgrade.inline_keyboard for btn in row]

    assert "Тип услуг" in repair_labels
    assert "Категория ремонта" in repair_labels
    assert "Категории апгрейда" not in repair_labels

    assert "Тип услуг" in upgrade_labels
    assert "Категории апгрейда" in upgrade_labels


@pytest.mark.asyncio
async def test_service_type_edit_to_upgrade_requests_upgrade_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as reg

    owner = types.SimpleNamespace(
        draft_upgrade_categories="Окраска",
        draft_category="Механика",
    )

    async def _fake_update_draft(*args, **kwargs):
        return None

    async def _fake_get_owner(_tg_id: int):
        return owner

    async def _fake_safe(_event, text: str, reply_markup=None):
        _fake_safe.last_text = text

    _fake_safe.last_text = ""

    monkeypatch.setattr(reg, "_update_draft", _fake_update_draft)
    monkeypatch.setattr(reg, "_get_owner", _fake_get_owner)
    monkeypatch.setattr(reg, "_safe_edit_or_answer", _fake_safe)

    callback = FakeCallback("reg_stype:upgrade", user_id=2001)
    state = FakeState({"editing_draft": True, "editing_draft_field": "service_type"})

    await reg.reg_service_type(callback, state)

    assert state.state == RegistrationFSM.reg_upgrade_categories
    assert "категории апгрейда" in _fake_safe.last_text.lower()


@pytest.mark.asyncio
async def test_service_type_edit_to_repair_requests_repair_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.registration as reg

    owner = types.SimpleNamespace(
        draft_upgrade_categories="Окраска",
        draft_category=None,
    )

    async def _fake_update_draft(*args, **kwargs):
        return None

    async def _fake_get_owner(_tg_id: int):
        return owner

    async def _fake_safe(_event, text: str, reply_markup=None):
        _fake_safe.last_text = text

    _fake_safe.last_text = ""

    monkeypatch.setattr(reg, "_update_draft", _fake_update_draft)
    monkeypatch.setattr(reg, "_get_owner", _fake_get_owner)
    monkeypatch.setattr(reg, "_safe_edit_or_answer", _fake_safe)

    callback = FakeCallback("reg_stype:repair", user_id=2002)
    state = FakeState({"editing_draft": True, "editing_draft_field": "service_type"})

    await reg.reg_service_type(callback, state)

    assert state.state == RegistrationFSM.reg_category
    assert "категорию ремонта" in _fake_safe.last_text.lower()


@pytest.mark.asyncio
async def test_sync_active_service_from_draft_updates_service_without_remoderation() -> (
    None
):
    import partner_bot.handlers.registration as reg

    bundle = await create_active_owner_bundle(service_type="repair")
    owner_user_id = bundle["owner_user_id"]
    service_id = bundle["service_id"]

    async with async_session() as session:
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()
        owner.draft_name = "Новый профиль партнера"
        owner.draft_service_type = "upgrade"
        owner.draft_category = None
        owner.draft_upgrade_categories = "Окраска,Прошивка"
        owner.draft_hydroisolation = True
        owner.draft_hydro_price = "1500-2000"
        await session.commit()

    svc = await reg._sync_active_service_from_draft(owner_user_id)

    async with async_session() as session:
        db_svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        db_owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert svc is not None
    assert db_svc.name == "Новый профиль партнера"
    assert db_svc.service_type == "upgrade"
    assert db_svc.category_id is None
    assert db_svc.upgrade_categories == "Окраска,Прошивка"
    assert db_svc.has_hydroisolation is True
    assert db_svc.hydroisolation_price == "1500-2000"
    assert db_owner.status == "активный"
    assert db_svc.partnership_status == "активный"


@pytest.mark.asyncio
async def test_profile_hydro_toggle_yes_without_price_requests_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.profile as profile

    bundle = await create_active_owner_bundle(service_type="repair")
    owner_user_id = bundle["owner_user_id"]
    service_id = bundle["service_id"]

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        svc.hydroisolation_price = None
        svc.has_hydroisolation = False

        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()
        owner.draft_hydroisolation = False
        owner.draft_hydro_price = None
        await session.commit()

    async def _fake_notify(*args, **kwargs):
        return None

    def _fake_schedule(*args, **kwargs):
        return None

    monkeypatch.setattr(profile, "_notify_admins_about_profile_update", _fake_notify)
    monkeypatch.setattr(profile, "_schedule_sheets_call", _fake_schedule)

    callback = FakeCallback("pedit:hydro:yes", user_id=owner_user_id)
    state = FakeState()

    await profile.profile_toggle_hydro(callback, state)

    assert state.state is not None
    assert callback.message.answers
    assert "укажите стоимость" in callback.message.answers[-1][0].lower()


@pytest.mark.asyncio
async def test_profile_hydro_toggle_no_clears_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import partner_bot.handlers.profile as profile

    bundle = await create_active_owner_bundle(service_type="repair")
    owner_user_id = bundle["owner_user_id"]
    service_id = bundle["service_id"]

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        svc.hydroisolation_price = "1200"
        svc.has_hydroisolation = True

        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()
        owner.draft_hydroisolation = True
        owner.draft_hydro_price = "1200"
        await session.commit()

    async def _fake_notify(*args, **kwargs):
        return None

    def _fake_schedule(*args, **kwargs):
        return None

    monkeypatch.setattr(profile, "_notify_admins_about_profile_update", _fake_notify)
    monkeypatch.setattr(profile, "_schedule_sheets_call", _fake_schedule)

    callback = FakeCallback("pedit:hydro:no", user_id=owner_user_id)
    state = FakeState()

    await profile.profile_toggle_hydro(callback, state)

    async with async_session() as session:
        svc = (
            await session.execute(select(Service).where(Service.id == service_id))
        ).scalar_one()
        owner = (
            await session.execute(
                select(ServiceDraft).where(ServiceDraft.owner_user_id == owner_user_id)
            )
        ).scalar_one()

    assert svc.has_hydroisolation is False
    assert svc.hydroisolation_price is None
    assert owner.draft_hydroisolation is False
    assert owner.draft_hydro_price is None
