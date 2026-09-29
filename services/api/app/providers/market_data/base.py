from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.providers.base import FetchResult


@dataclass(frozen=True)
class DailyBar:
    """One end-of-day bar in the provider's original values."""

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_open: Decimal | None = None
    adj_high: Decimal | None = None
    adj_low: Decimal | None = None
    adj_close: Decimal | None = None
    adj_volume: int | None = None
    dividend_cash: Decimal | None = None
    split_factor: Decimal | None = None


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    display_name: str
    license_note: str
    credential_env: str | None


class MarketDataProvider(Protocol):
    """Port implemented by every market-data adapter."""

    info: ProviderInfo

    def get_daily_bars(self, ticker: str, start: date, end: date) -> FetchResult[list[DailyBar]]:
        """Return validated daily bars in [start, end]. Raises ProviderError on failure."""
        ...


def bar_problems(bar: DailyBar) -> list[str]:
    """Structural checks on a bar. Rows that fail are rejected, never repaired."""

    problems: list[str] = []
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        problems.append("non_positive_price")
    if bar.high < max(bar.open, bar.close, bar.low):
        problems.append("high_below_range")
    if bar.low > min(bar.open, bar.close, bar.high):
        problems.append("low_above_range")
    if bar.volume < 0:
        problems.append("negative_volume")
    if bar.split_factor is not None and bar.split_factor <= 0:
        problems.append("non_positive_split_factor")
    return problems
