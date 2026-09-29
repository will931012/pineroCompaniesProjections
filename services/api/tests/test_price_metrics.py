from decimal import Decimal

from app.analytics.price_metrics import daily_change


def test_daily_change_is_exact_decimal_arithmetic() -> None:
    result = daily_change(Decimal("101.50"), Decimal("100.00"))

    assert result.change == Decimal("1.50")
    assert result.change_percent == Decimal("1.5")


def test_daily_change_negative_move() -> None:
    result = daily_change(Decimal("95"), Decimal("100"))

    assert result.change == Decimal("-5")
    assert result.change_percent == Decimal("-5")


def test_daily_change_percent_is_undefined_for_non_positive_base() -> None:
    result = daily_change(Decimal("1"), Decimal("0"))

    assert result.change == Decimal("1")
    assert result.change_percent is None
