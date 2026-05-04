from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.services.sheets_writer import _service_ready_for_export


def _service_stub(**overrides):
    data = {
        "partnership_status": "ожидает",
        "city": "Москва",
        "name": "Service",
        "service_type": "complex",
        "address": "Тестовый адрес",
        "phone": "+79990000000",
        "open_time": "10:00",
        "close_time": "20:00",
        "working_days": "Пн,Вт,Ср",
        "category_id": 1,
        "upgrade_categories": "",
        "nearest_metro": "",
        "has_hydroisolation": False,
        "hydroisolation_price": "",
        "diagnostics_price": 1000,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def test_service_ready_for_export_keeps_active_legacy_service() -> None:
    svc = _service_stub(
        partnership_status="активный",
        upgrade_categories="",
        nearest_metro="",
        diagnostics_price=None,
    )

    assert _service_ready_for_export(svc) is True


def test_service_ready_for_export_remains_strict_for_pending_service() -> None:
    svc = _service_stub(
        partnership_status="ожидает",
        upgrade_categories="",
        nearest_metro="ВДНХ",
    )

    assert _service_ready_for_export(svc) is False
