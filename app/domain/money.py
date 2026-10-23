"""Exact EUR-cent helpers shared by feature and risk calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from app.contracts import MAX_MONEY_CENTS, checked_money

_ONE_CENT = Decimal("1")
_ZERO = Decimal("0")
_ONE_HUNDRED = Decimal("100")


def as_decimal(value: Decimal | int, *, field: str = "number") -> Decimal:
    """Return a finite Decimal without routing exact inputs through binary float."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError(f"{field}_must_be_decimal_or_integer")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{field}_must_be_finite")
    return result


def checked_cents(value: int) -> int:
    """Validate an integer-cent amount, rejecting bool and unsafe JSON integers."""
    if type(value) is not int or not 0 <= value <= MAX_MONEY_CENTS:
        raise ValueError("money_out_of_range")
    return value


def checked_cents_sum(values: Iterable[int]) -> int:
    """Add amounts with an overflow check at every step."""
    total = 0
    for value in values:
        total = checked_money(total + checked_cents(value))
    return total


def round_cents_half_up(amount: Decimal | int) -> int:
    """Round a non-negative EUR-cent amount to a whole cent, ties away from zero."""
    exact = as_decimal(amount, field="amount")
    if exact < _ZERO:
        raise ValueError("amount_must_be_nonnegative")
    rounded = exact.quantize(_ONE_CENT, rounding=ROUND_HALF_UP)
    return checked_money(int(rounded))


def risk_money_cents(price_cents: int, score: Decimal | int) -> int:
    """Compute ``price * score / 100`` and round this service once to cents."""
    price = checked_cents(price_cents)
    exact_score = as_decimal(score, field="score")
    if not _ZERO <= exact_score <= _ONE_HUNDRED:
        raise ValueError("score_out_of_range")
    return round_cents_half_up(Decimal(price) * exact_score / _ONE_HUNDRED)
