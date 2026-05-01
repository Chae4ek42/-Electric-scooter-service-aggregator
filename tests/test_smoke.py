from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.core.database import async_session
from client_bot.domain.models import Brand, MetroStation, Model, ServiceCategory
from tests._helpers import init_db_once


@pytest.fixture(scope="module", autouse=True)
async def _init_db() -> None:
    await init_db_once()


def test_core_modules_importable() -> None:
    import client_bot.handlers.order  # noqa: F401
    import partner_bot.handlers.registration  # noqa: F401
    import partner_bot.handlers.profile  # noqa: F401
    import client_bot.services.sheets_sync  # noqa: F401


@pytest.mark.asyncio
async def test_seeded_entities_exist() -> None:
    async with async_session() as session:
        brands_count = (
            await session.execute(select(func.count(Brand.id)))
        ).scalar_one()
        models_count = (
            await session.execute(select(func.count(Model.id)))
        ).scalar_one()
        stations_count = (
            await session.execute(select(func.count(MetroStation.id)))
        ).scalar_one()
        categories = (await session.execute(select(ServiceCategory))).scalars().all()

    assert brands_count >= 1
    assert models_count >= 1
    assert stations_count >= 50
    assert {c.name for c in categories} >= {
        "Механика",
        "Электрика",
        "Электрика + механика",
    }


def test_client_keyboards_build_without_errors() -> None:
    from client_bot.ui.keyboards import (
        calendar_kb,
        confirm_kb,
        location_method_kb,
        main_menu_kb,
        service_type_kb,
        time_slots_kb,
    )

    assert main_menu_kb().keyboard
    assert service_type_kb().inline_keyboard
    assert location_method_kb().inline_keyboard
    assert confirm_kb().inline_keyboard
    assert calendar_kb().inline_keyboard
    assert time_slots_kb("01.01.2031").inline_keyboard


def test_partner_keyboards_build_without_errors() -> None:
    from partner_bot.ui.keyboards import (
        draft_edit_kb,
        profile_edit_fields_kb,
        reg_confirm_kb,
    )

    draft_labels = [
        btn.text for row in draft_edit_kb("repair").inline_keyboard for btn in row
    ]
    profile_labels = [
        btn.text for row in profile_edit_fields_kb().inline_keyboard for btn in row
    ]
    reg_labels = [
        btn.text
        for row in reg_confirm_kb(has_bank=False).inline_keyboard
        for btn in row
    ]

    assert "Тип услуг" in draft_labels
    assert "Telegram" not in profile_labels
    assert "Назад" in reg_labels


def test_metro_search_and_graph_smoke() -> None:
    from client_bot.services.metro_graph import metro_transfer_distance
    from client_bot.services.metro_search import best_metro_match
    from client_bot.domain.models import MetroStation

    stations = [
        MetroStation(id=1, name="Арбатская", line="Линия 1", lat=None, lon=None),
        MetroStation(id=2, name="Тверская", line="Линия 2", lat=None, lon=None),
    ]

    assert metro_transfer_distance("Арбатская", "Арбатская") == 0
    match = best_metro_match("арбат", stations)
    assert match is not None
    assert match.name == "Арбатская"


def test_city_search_dataset_smoke() -> None:
    from client_bot.services.city_search import best_city_match, city_candidates

    cities = city_candidates()

    assert len(cities) >= 1000
    assert "Долгопрудный" in cities
    assert best_city_match("долгопруд", cities) == "Долгопрудный"


def test_schema_validation_smoke() -> None:
    from client_bot.domain.schemas import MetroTextInput, ProblemDescription

    assert MetroTextInput(text="Арбатская").text == "Арбатская"
    assert ProblemDescription(text="Не едет колесо").text == "Не едет колесо"

    with pytest.raises(ValidationError):
        MetroTextInput(text="a")
    with pytest.raises(ValidationError):
        ProblemDescription(text="ab")
