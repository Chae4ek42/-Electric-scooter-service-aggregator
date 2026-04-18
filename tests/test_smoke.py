"""Automated smoke-tests for all bot modules.

Runs WITHOUT Telegram — verifies DB, seed, metro search, keyboards, states.
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func


async def test_all() -> None:
    from bot.core.database import async_session, engine
    from bot.domain.models import (
        Base,
        Brand,
        MetroStation,
        Model,
        Order,
        Service,
        ServiceCategory,
        User,
        UserAction,
    )
    from bot.services.seed import init_db

    print("=" * 60)
    print("ESAS Bot — Module Smoke Tests")
    print("=" * 60)

    # 1. DB init
    print("\n[1] Initialising database …")
    await init_db()
    print("    ✅ Database initialised")

    async with async_session() as session:
        # 2. Brands
        brands = (
            (await session.execute(select(Brand).order_by(Brand.name))).scalars().all()
        )
        print(f"\n[2] Brands loaded: {len(brands)}")
        assert len(brands) >= 5, f"Expected ≥5 brands, got {len(brands)}"
        print(f"    ✅ Brands OK: {[b.name for b in brands]}")

        # 3. Models
        total_models = (await session.execute(select(func.count(Model.id)))).scalar()
        print(f"\n[3] Total models: {total_models}")
        assert total_models >= 10, f"Expected ≥10 models, got {total_models}"
        # Check brand→model relation
        for b in brands[:2]:
            models = (
                (await session.execute(select(Model).where(Model.brand_id == b.id)))
                .scalars()
                .all()
            )
            print(f"    Brand '{b.name}': {len(models)} models")
            assert len(models) >= 1
        print("    ✅ Models OK")

        # 4. Service categories
        cats = (await session.execute(select(ServiceCategory))).scalars().all()
        print(f"\n[4] Service categories: {[c.name for c in cats]}")
        assert len(cats) == 2
        print("    ✅ Categories OK")

        # 5. Services
        repair_svcs = (
            (
                await session.execute(
                    select(Service).where(Service.service_type == "repair")
                )
            )
            .scalars()
            .all()
        )
        upgrade_svcs = (
            (
                await session.execute(
                    select(Service).where(Service.service_type == "upgrade")
                )
            )
            .scalars()
            .all()
        )
        print(
            f"\n[5] Repair services: {len(repair_svcs)}, Upgrade services: {len(upgrade_svcs)}"
        )
        # Services come from Google Sheet (not seeded); check model attributes
        for s in repair_svcs + upgrade_svcs:
            assert hasattr(
                s, "is_available"
            ), f"Service '{s.name}' missing is_available"
        print("    ✅ Services OK")

        # 6. Metro stations
        stations = (await session.execute(select(MetroStation))).scalars().all()
        print(f"\n[6] Metro stations: {len(stations)}")
        assert len(stations) >= 200, f"Expected ≥200 stations, got {len(stations)}"
        print("    ✅ Metro stations OK")

    # 7. Metro fuzzy search
    print("\n[7] Testing metro fuzzy search …")
    from bot.services.metro_search import best_metro_match, top_metro_matches

    async with async_session() as session:
        stations = (await session.execute(select(MetroStation))).scalars().all()

    test_queries = [
        ("арбат", True),
        ("тверск", True),
        ("сокольники", True),
        ("ВДНХ", True),
        ("парк культ", True),
        ("митино", True),
        ("несуществующая123", False),
        ("ком", True),  # Коммунарка / Комсомольская
        ("белор", True),  # Белорусская
        ("ку", True),  # Кунцевская etc.
    ]
    for query, should_find in test_queries:
        match = best_metro_match(query, stations)
        if should_find:
            assert match is not None, f"Expected match for '{query}', got None"
            print(f"    '{query}' → {match.name} ({match.line})")
        else:
            print(
                f"    '{query}' → {'None' if match is None else match.name} (expected no match: {'✅' if match is None else '⚠️ unexpected match'})"
            )

    # Test top_metro_matches
    matches = top_metro_matches("бауман", stations, limit=3)
    print(f"    top('бауман'): {[(m.name, f'{s:.2f}') for m, s in matches]}")
    assert len(matches) >= 1
    print("    ✅ Metro search OK")

    # 8. Keyboards
    print("\n[8] Testing keyboard builders …")
    from bot.ui.keyboards import (
        calendar_kb,
        confirm_kb,
        location_method_kb,
        main_menu_kb,
        malfunction_type_kb,
        service_type_kb,
        time_slots_kb,
    )

    assert main_menu_kb() is not None
    assert service_type_kb() is not None
    assert malfunction_type_kb() is not None
    assert location_method_kb() is not None
    assert confirm_kb() is not None

    cal = calendar_kb()
    # Today may or may not be included (depends on Moscow time vs WORK_HOUR_END)
    from bot.core.config import CALENDAR_DAYS

    total_cal_btns = sum(len(row) for row in cal.inline_keyboard)
    # 14 date buttons (no today) + 1 back = 15  OR  15 date buttons (with today) + 1 back = 16
    assert (
        CALENDAR_DAYS + 1 <= total_cal_btns <= CALENDAR_DAYS + 2
    ), f"Calendar has {total_cal_btns} buttons, expected {CALENDAR_DAYS + 1} or {CALENDAR_DAYS + 2}"
    print(
        f"    Calendar: {total_cal_btns} buttons ({total_cal_btns - 1} dates + 1 back)"
    )

    ts = time_slots_kb("01.01.2026")
    total_ts_btns = sum(len(row) for row in ts.inline_keyboard)
    print(f"    Time slots: {total_ts_btns} buttons")
    assert total_ts_btns >= 5  # at least 5 time slots
    print("    ✅ Keyboards OK")

    # 9. FSM states
    print("\n[9] Testing FSM states …")
    from bot.domain.states import OrderFSM

    states = [
        OrderFSM.service_type,
        OrderFSM.brand,
        OrderFSM.model,
        OrderFSM.malfunction_type,
        OrderFSM.upgrade_category,
        OrderFSM.problem_description,
        OrderFSM.location_method,
        OrderFSM.metro_search,
        OrderFSM.metro_confirm,
        OrderFSM.calendar_date,
        OrderFSM.calendar_time,
        OrderFSM.confirm,
    ]
    for s in states:
        assert s.state is not None, f"State {s} has no state string"
    print(f"    {len(states)} states defined")
    print("    ✅ FSM states OK")

    # 10. Pydantic schemas
    print("\n[10] Testing Pydantic schemas …")
    from pydantic import ValidationError
    from bot.domain.schemas import MetroTextInput, ProblemDescription

    # Valid
    assert MetroTextInput(text="Арбатская").text == "Арбатская"
    assert ProblemDescription(text="Не работает руль").text == "Не работает руль"

    # Invalid
    try:
        MetroTextInput(text="a")
        assert False, "Should have raised"
    except ValidationError:
        pass

    try:
        ProblemDescription(text="ab")
        assert False, "Should have raised"
    except ValidationError:
        pass
    print("    ✅ Schemas OK")

    # 11. User creation & order creation test
    print("\n[11] Testing User + Order DB operations …")
    async with async_session() as session:
        test_user = User(id=999999999, username="testbot", full_name="Test User")
        session.add(test_user)
        await session.flush()

        # Pick first service and model (create temp service if empty DB)
        svc = (await session.execute(select(Service).limit(1))).scalar_one_or_none()
        created_svc = False
        if svc is None:
            cat = (await session.execute(select(ServiceCategory).limit(1))).scalar_one()
            svc = Service(
                name="Test Service",
                service_type="repair",
                category_id=cat.id,
                is_available=True,
                address="Test Address, 1",
            )
            session.add(svc)
            await session.flush()
            created_svc = True
        mdl = (await session.execute(select(Model).limit(1))).scalar_one()

        order = Order(
            user_id=test_user.id,
            service_id=svc.id,
            model_id=mdl.id,
            metro_station="Арбатская",
            scheduled_date="10.04.2026",
            scheduled_time="14:00",
            status="awaiting_payment",
        )
        session.add(order)
        await session.flush()
        assert order.id is not None
        print(f"    Created test order #{order.id}")

        # Read back
        o = (
            await session.execute(select(Order).where(Order.id == order.id))
        ).scalar_one()
        assert o.metro_station == "Арбатская"
        assert o.service.name == svc.name
        assert o.model.name == mdl.name
        print(f"    Order verified: service='{o.service.name}', model='{o.model.name}'")

        # Cleanup
        await session.delete(order)
        if created_svc:
            await session.delete(svc)
        await session.delete(test_user)
        await session.commit()
        print("    ✅ User + Order OK")

    # 12. UserAction logging test
    print("\n[12] Testing UserAction logging …")
    async with async_session() as session:
        action = UserAction(
            user_id=123456,
            state="OrderFSM:brand",
            action_type="button_click",
            payload="brand:1",
            status="success",
        )
        session.add(action)
        await session.commit()
        assert action.id is not None
        print(f"    Logged action #{action.id}")
        await session.delete(action)
        await session.commit()
        print("    ✅ UserAction OK")

    # 13. Service owner-related columns
    print("\n[13] Testing Service owner columns …")
    from bot.domain.models import Service as SVC

    svc_cols = {c.name for c in SVC.__table__.columns}
    assert "status" in svc_cols, "Service must have 'status' column"
    assert "telegram_id" in svc_cols, "Service must have 'telegram_id' column"
    print("    Service has status & telegram_id columns")

    from partner_bot.handlers.admin import _STATUS_RU

    expected_keys = {"ожидает", "активный", "отклонён", "приостановлен"}
    assert (
        set(_STATUS_RU.keys()) == expected_keys
    ), f"_STATUS_RU keys = {set(_STATUS_RU.keys())}, expected {expected_keys}"
    print(f"    _STATUS_RU keys: {sorted(_STATUS_RU.keys())}")
    print("    ✅ Russian statuses OK")

    # 14. Redis config
    print("\n[14] Testing Redis config …")
    from bot.core.config import REDIS_URL

    assert REDIS_URL.startswith("redis://"), f"REDIS_URL = '{REDIS_URL}'"
    print(f"    REDIS_URL = {REDIS_URL}")
    print("    ✅ Redis config OK")

    # 15. ThrottlingMiddleware has Redis support
    print("\n[15] Testing ThrottlingMiddleware (Redis with fallback) …")
    from bot.core.middlewares import ThrottlingMiddleware

    mw = ThrottlingMiddleware()
    assert hasattr(mw, "_KEY_PREFIX"), "ThrottlingMiddleware missing _KEY_PREFIX"
    assert hasattr(mw, "_last"), "ThrottlingMiddleware missing _last fallback dict"
    print("    ThrottlingMiddleware: _KEY_PREFIX for Redis, _last for fallback")
    print("    ✅ Throttling middleware OK")

    # 16. SHEETS_COLUMNS config
    print("\n[16] Testing SHEETS_COLUMNS config …")
    from bot.core.config import SHEETS_COLUMNS

    assert isinstance(SHEETS_COLUMNS, list), "SHEETS_COLUMNS must be a list"
    assert len(SHEETS_COLUMNS) >= 10, f"Expected ≥10 columns, got {len(SHEETS_COLUMNS)}"
    cols_lower = [c.lower() for c in SHEETS_COLUMNS]
    assert "название" in cols_lower, "SHEETS_COLUMNS missing 'Название'"
    assert "доступен" in cols_lower, "SHEETS_COLUMNS missing 'Доступен'"
    print(f"    SHEETS_COLUMNS: {len(SHEETS_COLUMNS)} columns")
    print(f"    First 5: {SHEETS_COLUMNS[:5]}")
    print("    ✅ SHEETS_COLUMNS OK")

    # 17. Service.main_brand_scooter field
    print("\n[17] Testing Service.main_brand_scooter field …")
    svc_cols = {c.name for c in Service.__table__.columns}
    assert "main_brand_scooter" in svc_cols, "Service missing main_brand_scooter column"
    print("    Service.main_brand_scooter present")
    print("    ✅ main_brand_scooter OK")

    # 18. sheets_writer uses SHEETS_COLUMNS
    print("\n[18] Testing sheets_writer column mapping …")
    from bot.services.sheets_writer import _FIELD_GETTERS, _service_to_row

    for col in SHEETS_COLUMNS:
        assert col.strip().lower() in _FIELD_GETTERS, f"No getter for column '{col}'"
    print(f"    All {len(SHEETS_COLUMNS)} columns have getters")
    print("    ✅ sheets_writer OK")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_all())
