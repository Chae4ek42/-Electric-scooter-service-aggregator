from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


MONEY_QUANT = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")
HYDRO_PREPAYMENT = Decimal("500.00")


ORDER_STATUS_AWAITING_PAYMENT = "awaiting_payment"
ORDER_STATUS_PAID = "paid"
ORDER_STATUS_ACCEPTED = "accepted"
ORDER_STATUS_IN_PROGRESS = "in_progress"
ORDER_STATUS_READY_FOR_PICKUP = "ready_for_pickup"
ORDER_STATUS_COMPLETED = "completed"
ORDER_STATUS_CANCELLED = "cancelled"
ORDER_STATUS_REJECTED_BY_PARTNER = "rejected_by_partner"
ORDER_STATUS_INTERRUPTED = "interrupted"
ORDER_STATUS_CLIENT_REFUSED = "client_refused"
ORDER_STATUS_DISPUTED = "disputed"
ORDER_STATUS_NO_CENTER = "no_center"


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ORDER_STATUS_AWAITING_PAYMENT: {
        ORDER_STATUS_PAID,
        ORDER_STATUS_ACCEPTED,
        ORDER_STATUS_CANCELLED,
        ORDER_STATUS_REJECTED_BY_PARTNER,
    },
    ORDER_STATUS_PAID: {
        ORDER_STATUS_ACCEPTED,
        ORDER_STATUS_CANCELLED,
        ORDER_STATUS_REJECTED_BY_PARTNER,
    },
    ORDER_STATUS_ACCEPTED: {
        ORDER_STATUS_IN_PROGRESS,
        ORDER_STATUS_CLIENT_REFUSED,
        ORDER_STATUS_CANCELLED,
        ORDER_STATUS_REJECTED_BY_PARTNER,
    },
    ORDER_STATUS_IN_PROGRESS: {
        ORDER_STATUS_READY_FOR_PICKUP,
    },
    ORDER_STATUS_READY_FOR_PICKUP: {
        ORDER_STATUS_COMPLETED,
        ORDER_STATUS_DISPUTED,
    },
    ORDER_STATUS_COMPLETED: set(),
    ORDER_STATUS_CANCELLED: set(),
    ORDER_STATUS_REJECTED_BY_PARTNER: set(),
    ORDER_STATUS_INTERRUPTED: set(),
    ORDER_STATUS_CLIENT_REFUSED: set(),
    ORDER_STATUS_DISPUTED: set(),
    ORDER_STATUS_NO_CENTER: set(),
}


def money(value: Decimal | float | int | str | None) -> Decimal:
    if value is None:
        return ZERO_MONEY
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Некорректная денежная сумма: {value}") from exc
    return dec.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_to_float(value: Decimal | float | int | str | None) -> float:
    return float(money(value))


def prepayment_amount(
    upgrade_category: str | None, diagnostics_price: float | None
) -> Decimal:
    if upgrade_category == "Гидроизоляция":
        return HYDRO_PREPAYMENT
    if diagnostics_price is None:
        return ZERO_MONEY
    return money(diagnostics_price)


def remainder_amount(
    total_cost: Decimal | float | int | str | None,
    prepayment: Decimal | float | int | str | None,
) -> Decimal:
    total = money(total_cost)
    paid = money(prepayment)
    remainder = total - paid
    if remainder < ZERO_MONEY:
        return ZERO_MONEY
    return remainder


def parse_hydro_price_range(raw: str | None) -> tuple[Decimal, Decimal] | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(" ", "")
    if not cleaned:
        return None

    if "-" in cleaned:
        parts = cleaned.split("-", maxsplit=1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                "Цена гидроизоляции должна быть числом или диапазоном вида 1000-2000"
            )
        left = money(parts[0])
        right = money(parts[1])
        if left > right:
            raise ValueError(
                "В диапазоне гидроизоляции левая граница не может быть больше правой"
            )
        return left, right

    single = money(cleaned)
    return single, single


def is_within_hydro_range(
    total_cost: Decimal | float | int | str | None, hydro_raw: str | None
) -> bool:
    rng = parse_hydro_price_range(hydro_raw)
    if rng is None:
        return True
    amount = money(total_cost)
    low, high = rng
    return low <= amount <= high


def can_order_transition(current_status: str | None, new_status: str) -> bool:
    if current_status is None:
        return False
    if current_status == new_status:
        return True
    allowed = _ALLOWED_TRANSITIONS.get(current_status)
    if allowed is None:
        return False
    return new_status in allowed


def ensure_order_transition(current_status: str | None, new_status: str) -> None:
    if not can_order_transition(current_status, new_status):
        raise ValueError(
            f"Недопустимый переход статуса заказа: {current_status!r} -> {new_status!r}"
        )


def set_order_status(order: Any, new_status: str, *, force: bool = False) -> None:
    current = getattr(order, "status", None)
    if not force:
        ensure_order_transition(current, new_status)
    order.status = new_status
