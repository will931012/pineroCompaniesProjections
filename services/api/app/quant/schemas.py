import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

# --- Markets ------------------------------------------------------------------------------


class SeriesPoint(BaseModel):
    date: date
    value: float


class EtfOut(BaseModel):
    ticker: str
    name: str
    group: str  # index | sector | other
    close: float | None
    price_date: date | None
    returns: dict[str, float | None]  # 1d, 1w, 1m, 3m, ytd, 1y
    spark: list[SeriesPoint]


class CurvePoint(BaseModel):
    tenor: str
    months: float
    value: float


class CurveOut(BaseModel):
    observed_on: date
    points: list[CurvePoint]


class MacroSeriesOut(BaseModel):
    series_id: str
    label: str
    available: bool
    source: str | None = None
    unit: str | None = None
    value: float | None = None
    observation_date: date | None = None
    available_on: date | None = None
    yoy_change: float | None = None
    history: list[SeriesPoint] = []


class RegimeOut(BaseModel):
    as_of: date
    label: str
    components: dict[str, Any]
    history: list[tuple[date, str]]


class BitcoinTile(BaseModel):
    close: float
    as_of: date
    change_1d: float | None
    change_30d: float | None
    drawdown: float


class MarketsOut(BaseModel):
    etfs: list[EtfOut]
    curve: CurveOut | None
    curve_year_ago: CurveOut | None
    macro: list[MacroSeriesOut]
    macro_configured: bool
    regime: RegimeOut | None
    bitcoin: BitcoinTile | None
    notes: list[str]


# --- Bitcoin ------------------------------------------------------------------------------


class BitcoinPoint(BaseModel):
    date: date
    close: float
    sma_50: float | None
    sma_200: float | None
    drawdown: float


class RelationshipOut(BaseModel):
    name: str
    measure: str
    window_days: int
    correlation: float | None
    beta: float | None
    observations: int
    rolling: list[tuple[date, float | None]]


class HalvingOut(BaseModel):
    halving: date
    price_at_halving: float
    days_observed: int
    path: list[tuple[int, float]]
    return_1y: float | None
    peak_multiple: float | None
    days_to_peak: int | None
    drawdown_after_peak: float | None


class BitcoinOut(BaseModel):
    available: bool
    summary: dict[str, Any]
    series: list[BitcoinPoint]
    rsi_14: list[tuple[date, float | None]]
    relationships: list[RelationshipOut]
    halvings: list[HalvingOut]
    next_halving_note: str
    source: str
    notes: list[str]


# --- Company research ---------------------------------------------------------------------


class TechnicalPoint(BaseModel):
    date: date
    close: float
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    drawdown: float
    relative: float | None  # growth of the stock ÷ growth of SPY, rebased to 1


class TechnicalsOut(BaseModel):
    ticker: str
    available: bool
    message: str | None
    price_date: date | None
    stats: dict[str, float | None]
    series: list[TechnicalPoint]
    source: str


class FactorInput(BaseModel):
    feature: str
    value: float | None
    z: float


class FactorOut(BaseModel):
    key: str
    label: str
    score: float
    percentile: float
    inputs: list[FactorInput]
    history: list[SeriesPoint]  # percentile by month end


class FactorsOut(BaseModel):
    ticker: str
    as_of: date | None
    in_universe: bool
    universe_rank: int | None
    universe_size: int | None
    factors: list[FactorOut]
    note: str


class HolderOut(BaseModel):
    filer_name: str
    filer_cik: int
    shares: float
    value_usd: float
    previous_shares: float | None
    change: str  # new | increased | decreased | unchanged
    filing_date: date
    accession: str


class OwnershipPeriodOut(BaseModel):
    period: date
    holders: int
    shares: float
    value_usd: float
    share_of_market_cap: float | None


class OwnershipOut(BaseModel):
    ticker: str
    available: bool
    periods: list[OwnershipPeriodOut]
    holders: list[HolderOut]
    sold_out: list[HolderOut]
    cusips: list[str]
    note: str


class DriverOut(BaseModel):
    feature: str
    value: float | None
    percentile: float | None
    contribution: float


class PredictionOut(BaseModel):
    model_id: uuid.UUID
    model_name: str
    target: str
    horizon_days: int
    as_of: date
    probability: float
    probability_raw: float
    expected_excess: float | None
    excess_low: float | None
    excess_high: float | None
    drivers: list[DriverOut]
    data_available_on: date
    trained_through: date
    calibration: str
    model_oos_auc: float | None
    model_oos_rank_ic: float | None
    # False when the model failed the out-of-sample validation rule (see the disclaimer).
    validated: bool


class PredictionHistoryOut(BaseModel):
    as_of: date
    model_name: str
    probability: float
    expected_excess: float | None
    realised_excess: float | None
    went_up: bool | None


class PredictionsOut(BaseModel):
    ticker: str
    in_universe: bool
    predictions: list[PredictionOut]
    history: list[PredictionHistoryOut]
    disclaimer: str


# --- Models -------------------------------------------------------------------------------


class ModelSummary(BaseModel):
    id: uuid.UUID
    name: str
    target: str
    horizon_days: int
    status: str
    created_at: datetime
    trained_from: date
    trained_through: date
    feature_set_version: str
    code_version: str
    oos: dict[str, Any]
    live: dict[str, Any]


class ModelDetail(ModelSummary):
    params: dict[str, Any]
    evaluation: dict[str, Any]
    calibration: dict[str, Any]
    importance: dict[str, Any]
    feature_names: list[str]


class ResearchStatus(BaseModel):
    universe_dates: int
    latest_universe_date: date | None
    members_latest: int
    members_with_prices_latest: int
    survivorship: list[dict[str, Any]]
    price_targets: int
    prices_loaded: int
    prices_not_found: int
    feature_dates: int
    latest_feature_date: date | None
    fred_configured: bool
    macro_series_loaded: int
    bitcoin_days: int
    ownership_periods: list[date]
    models: int
