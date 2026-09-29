"""Deterministic price calculations. Pure functions; no I/O, no estimation."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DailyChange:
    change: Decimal
    change_percent: Decimal | None


def daily_change(last_close: Decimal, previous_close: Decimal) -> DailyChange:
    """Absolute and percentage change between two consecutive closes.

    change         = last_close - previous_close
    change_percent = change / previous_close * 100   (undefined when previous_close <= 0)
    """

    change = last_close - previous_close
    if previous_close <= 0:
        return DailyChange(change, None)
    return DailyChange(change, change / previous_close * Decimal(100))
