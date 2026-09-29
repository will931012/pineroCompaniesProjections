from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.companies.schemas import SourceRef


class DailyBarOut(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_open: float | None
    adj_high: float | None
    adj_low: float | None
    adj_close: float | None
    dividend_cash: float | None
    split_factor: float | None
    fetch_id: int


class PriceSummary(BaseModel):
    as_of: date
    last_close: float
    previous_close: float | None
    change: float | None
    change_percent: float | None
    # "adjusted" uses the provider's split/dividend-adjusted closes for the change.
    basis: Literal["adjusted", "raw"]


class DataQuality(BaseModel):
    status: Literal["fresh", "cached", "stale"]
    rejected_count: int
    warnings: list[str]


class MarketBarsResponse(BaseModel):
    ticker: str
    interval: Literal["1d"]
    provider: str
    bars: list[DailyBarOut]
    summary: PriceSummary | None
    quality: DataQuality
    sources: list[SourceRef]
