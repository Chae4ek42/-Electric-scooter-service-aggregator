from __future__ import annotations

from decimal import Decimal

from client_bot.domain.order_rules import (
    is_within_hydro_range,
    money,
    parse_hydro_price_range,
    prepayment_amount,
    remainder_amount,
)


class PaymentPolicy:
    @staticmethod
    def total(value: Decimal | float | int | str | None) -> Decimal:
        return money(value)

    @staticmethod
    def prepayment(
        upgrade_category: str | None, diagnostics_price: float | None
    ) -> Decimal:
        return prepayment_amount(upgrade_category, diagnostics_price)

    @staticmethod
    def remainder(
        total_cost: Decimal | float | int | str | None,
        upgrade_category: str | None,
        diagnostics_price: float | None,
    ) -> Decimal:
        total = money(total_cost)
        prepayment = prepayment_amount(upgrade_category, diagnostics_price)
        return remainder_amount(total, prepayment)

    @staticmethod
    def validate_hydro_total(
        total_cost: Decimal | float | int | str | None,
        hydro_price_raw: str | None,
    ) -> tuple[bool, tuple[Decimal, Decimal] | None]:
        rng = parse_hydro_price_range(hydro_price_raw)
        if rng is None:
            return True, None
        return is_within_hydro_range(total_cost, hydro_price_raw), rng
