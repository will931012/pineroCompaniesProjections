import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Rate = Annotated[float, Field(ge=-1.0, le=1.0)]
Positive = Annotated[float, Field(gt=0)]
Amount = Annotated[float, Field(ge=0)]


class CapmIn(BaseModel):
    risk_free: Annotated[float, Field(ge=-0.02, le=0.25)]
    beta: Annotated[float, Field(ge=0.0, le=5.0)]
    equity_risk_premium: Annotated[float, Field(ge=0.0, le=0.20)]
    pre_tax_cost_of_debt: Annotated[float, Field(ge=0.0, le=0.40)]
    equity_value: Amount
    debt_value: Amount


class DcfIn(BaseModel):
    base_revenue: Positive
    growth: Annotated[float, Field(ge=-0.5, le=1.0)]
    margin: Annotated[float, Field(ge=-1.0, le=0.9)]
    long_run_margin: Annotated[float, Field(ge=-0.5, le=0.9)]
    terminal_growth: Annotated[float, Field(ge=-0.05, le=0.06)]
    tax_rate: Annotated[float, Field(ge=0.0, le=0.6)]
    sales_to_capital: Annotated[float, Field(gt=0, le=20)]
    terminal_roic: Annotated[float, Field(gt=0, le=1.0)] | None = None
    debt: Amount
    cash: Amount
    shares: Positive
    mid_year: bool = False


class RimIn(BaseModel):
    book_value: Positive
    roe: Annotated[float, Field(ge=-0.5, le=0.8)]
    long_run_roe: Annotated[float, Field(ge=-0.2, le=0.5)] | None = None
    payout_ratio: Annotated[float, Field(ge=0.0, le=1.0)]
    terminal_growth: Annotated[float, Field(ge=-0.05, le=0.06)]
    shares: Positive


class DdmIn(BaseModel):
    dividend_per_share: Positive
    growth: Annotated[float, Field(ge=-0.3, le=0.5)]
    terminal_growth: Annotated[float, Field(ge=-0.05, le=0.06)]


class ScenarioIn(BaseModel):
    growth: Rate = 0.0
    margin: Rate = 0.0
    discount_rate: Annotated[float, Field(ge=-0.1, le=0.1)] = 0.0
    terminal_growth: Annotated[float, Field(ge=-0.03, le=0.03)] = 0.0


class ScenariosIn(BaseModel):
    bear: ScenarioIn = Field(
        default_factory=lambda: ScenarioIn(
            growth=-0.03, margin=-0.02, discount_rate=0.01, terminal_growth=-0.005
        )
    )
    bull: ScenarioIn = Field(
        default_factory=lambda: ScenarioIn(
            growth=0.03, margin=0.02, discount_rate=-0.01, terminal_growth=0.005
        )
    )


class ValuationIn(BaseModel):
    model: Literal["dcf", "rim", "ddm"]
    dcf: DcfIn | None = None
    rim: RimIn | None = None
    ddm: DdmIn | None = None
    capm: CapmIn
    # Replaces the computed WACC (DCF) or cost of equity (RIM, DDM) when set.
    discount_rate_override: Annotated[float, Field(gt=0, le=0.5)] | None = None
    scenarios: ScenariosIn = Field(default_factory=ScenariosIn)
    # Kept with a saved run so others can see where each default came from.
    sources: dict[str, str] = Field(default_factory=dict, max_length=60)


class SaveRunIn(ValuationIn):
    name: str = Field(min_length=1, max_length=120)


class DefaultsOut(BaseModel):
    ticker: str
    recommended: Literal["dcf", "rim", "ddm"]
    available: list[Literal["dcf", "rim", "ddm"]]
    unavailable: dict[str, str]
    dcf: DcfIn | None
    rim: RimIn | None
    ddm: DdmIn | None
    capm: CapmIn | None
    scenarios: ScenariosIn
    sources: dict[str, str]
    warnings: list[str]
    price: float | None
    price_date: date | None
    basis: str | None
    formula_version: str


class YearOut(BaseModel):
    year: int
    revenue: float | None
    growth: float | None
    margin: float | None
    operating_income: float | None
    nopat: float | None
    reinvestment: float | None
    cash_flow: float
    discount_factor: float
    present_value: float


class ResultOut(BaseModel):
    per_share: float | None
    enterprise_value: float | None
    equity_value: float | None
    pv_explicit: float
    pv_terminal: float
    terminal_value: float
    terminal_share: float
    discount_rate: float
    terminal_growth: float
    years: list[YearOut]


class ScenarioOut(BaseModel):
    name: Literal["bear", "base", "bull"]
    result: ResultOut | None
    # Why the scenario has no value (e.g. discount rate not above terminal growth).
    error: str | None
    # Per-share value ÷ price − 1, when a price is stored.
    upside: float | None


class GridOut(BaseModel):
    row_label: str
    column_label: str
    rows: list[float]
    columns: list[float]
    values: list[list[float | None]]


class RatesOut(BaseModel):
    cost_of_equity: float
    after_tax_cost_of_debt: float | None
    equity_weight: float | None
    discount_rate: float
    discount_rate_source: Literal["computed", "override"]


class ValuationOut(BaseModel):
    ticker: str
    model: Literal["dcf", "rim", "ddm"]
    rates: RatesOut
    scenarios: list[ScenarioOut]
    sensitivity: list[GridOut]
    price: float | None
    price_date: date | None
    formula_version: str


class RunSummary(BaseModel):
    id: uuid.UUID
    name: str
    model: Literal["dcf", "rim", "ddm"]
    created_at: datetime
    base_per_share: float | None
    price: float | None
    formula_version: str


class RunOut(RunSummary):
    request: dict[str, Any]
    sources: dict[str, Any]
    results: ValuationOut


class MultipleOut(BaseModel):
    key: str
    label: str
    company: float | None
    peer_median: float | None
    peer_count: int
    industry_median: float | None
    industry_count: int
    history_median: float | None
    history_years: int
    # Per-share value implied by applying the peer median to the company's own figure.
    implied_per_share: float | None
    denominator: str


class RelativeOut(BaseModel):
    ticker: str
    peer_basis: str
    industry_basis: str | None
    peers: list[str]
    multiples: list[MultipleOut]
    price: float | None
    price_date: date | None
    note: str
