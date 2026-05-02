from __future__ import annotations

from types import SimpleNamespace

from client_bot.ui.keyboards import order_select_kb
from partner_bot.ui.keyboards import partner_orders_list_kb


def test_client_order_select_keyboard_includes_order_code() -> None:
    order = SimpleNamespace(id=42, order_code="420042")
    kb = order_select_kb([order], "cancel")

    assert kb.inline_keyboard
    assert "код 420042" in kb.inline_keyboard[0][0].text


def test_partner_orders_list_keyboard_has_six_digit_fallback_code() -> None:
    order = SimpleNamespace(
        id=7,
        order_code=None,
        scheduled_date="01.01.2030",
        scheduled_time="10:30",
    )
    kb = partner_orders_list_kb([order], page=0, total_pages=1)

    assert kb.inline_keyboard
    assert "#7/000007" in kb.inline_keyboard[0][0].text
