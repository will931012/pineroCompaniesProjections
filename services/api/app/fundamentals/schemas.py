from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.companies.schemas import SourceRef

FundamentalsStatus = Literal[
    "current", "stale", "unavailable", "not_configured", "not_applicable", "not_available"
]


class PeriodOut(BaseModel):
    key: str
    label: str
    fiscal_year: int
    fiscal_quarter: int | None
    start: date
    end: date


class CellOut(BaseModel):
    period_key: str
    value: float
    # XBRL concept that supplied the value; null for values derived from other line items.
    concept: str | None
    accession: str | None
    filed_date: date
    derivation: str | None


class LineItemOut(BaseModel):
    key: str
    label: str
    unit: Literal["USD", "USD/shares", "shares"]
    cells: list[CellOut]


class StatementsOut(BaseModel):
    income: list[LineItemOut]
    balance: list[LineItemOut]
    cash_flow: list[LineItemOut]


class MetricPointOut(BaseModel):
    period_key: str
    value: float


class MetricSeriesOut(BaseModel):
    key: str
    label: str
    category: str
    unit: Literal["percent", "ratio", "currency", "shares", "per_share"]
    formula: str
    values: list[MetricPointOut]


class FundamentalsResponse(BaseModel):
    ticker: str
    cik: int | None
    period_type: Literal["annual", "quarterly"]
    as_of: date | None
    status: FundamentalsStatus
    message: str | None
    mapping_version: str
    formula_version: str
    periods: list[PeriodOut]
    statements: StatementsOut
    metrics: list[MetricSeriesOut]
    sources: list[SourceRef]


class SnapshotMetricOut(BaseModel):
    key: str
    label: str
    category: str
    unit: Literal["percent", "ratio", "currency", "multiple"]
    description: str
    value: float
    basis: str
    period_end: date | None
    available_date: date | None
    price_based: bool


class CompanyMetricsResponse(BaseModel):
    ticker: str
    computed_at: datetime | None
    formula_version: str | None
    metrics: list[SnapshotMetricOut]


class PeerRow(BaseModel):
    ticker: str
    name: str
    sic_code: str | None
    industry: str | None
    is_subject: bool
    metrics: dict[str, float]


class PeersResponse(BaseModel):
    ticker: str
    basis: str
    columns: list[str]
    rows: list[PeerRow]
