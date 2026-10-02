"""Deterministic intrinsic-value models. Pure functions over floats; no I/O.

DCF (free cash flow to the firm), two stages over ten years:
  years 1-5   revenue grows at g1 and the operating margin is m1;
  years 6-10  growth fades linearly from g1 to the terminal growth gT, and the margin from m1
              to the long-run margin mL.
  FCFF_t      = EBIT_t × (1 − tax) − reinvestment_t, reinvestment_t = ΔRevenue_t / sales-to-capital.
  Terminal    FCFF_11 = NOPAT_11 × (1 − gT / RONIC); TV = FCFF_11 / (WACC − gT), discounted
              with year 10's factor. RONIC (return on new invested capital) defaults to WACC,
              i.e. growth beyond year 10 creates no value.
  Equity      = enterprise value − debt + cash; per share = equity / diluted shares.

Residual income (banks and insurers, where cash flow to the firm is not meaningful):
  value = BV0 + Σ (ROE_t − ke) × BV_{t−1} discounted at ke, with BV growing by retained
  earnings; ROE holds for years 1-5 and fades to the long-run ROE over years 6-10.

Dividend discount: dividends per share grow at g1 for years 1-5, fade to gT over years 6-10,
then a Gordon terminal value; discounted at the cost of equity.

WACC (CAPM): ke = rf + β × ERP; WACC = E/(D+E) × ke + D/(D+E) × kd × (1 − tax).
"""

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

FORMULA_VERSION = "2026.10-1"
YEARS = 10
STAGE_ONE = 5
# The discount rate must exceed terminal growth by at least this much for a finite value.
MIN_SPREAD = 0.005

Model = Literal["dcf", "rim", "ddm"]


class ValuationError(ValueError):
    """Assumptions that cannot produce a value (the message says which and why)."""


@dataclass(frozen=True)
class WaccInputs:
    risk_free: float
    beta: float
    equity_risk_premium: float
    pre_tax_cost_of_debt: float
    tax_rate: float
    equity_value: float
    debt_value: float

    def cost_of_equity(self) -> float:
        return self.risk_free + self.beta * self.equity_risk_premium

    def wacc(self) -> float:
        total = self.equity_value + self.debt_value
        if total <= 0:
            raise ValuationError("Equity plus debt must be positive to weight the cost of capital.")
        weight_debt = self.debt_value / total
        after_tax_debt = self.pre_tax_cost_of_debt * (1 - self.tax_rate)
        return (1 - weight_debt) * self.cost_of_equity() + weight_debt * after_tax_debt


@dataclass(frozen=True)
class Scenario:
    """Shifts applied to the base case, in absolute terms (0.03 = 3 percentage points)."""

    growth: float = 0.0
    margin: float = 0.0
    discount_rate: float = 0.0
    terminal_growth: float = 0.0


DEFAULT_SCENARIOS: dict[str, Scenario] = {
    "bear": Scenario(growth=-0.03, margin=-0.02, discount_rate=0.01, terminal_growth=-0.005),
    "base": Scenario(),
    "bull": Scenario(growth=0.03, margin=0.02, discount_rate=-0.01, terminal_growth=0.005),
}


@dataclass(frozen=True)
class DcfAssumptions:
    base_revenue: float
    growth: float  # g1
    margin: float  # m1
    long_run_margin: float  # mL
    terminal_growth: float  # gT
    tax_rate: float
    sales_to_capital: float
    discount_rate: float  # WACC
    terminal_roic: float | None  # RONIC; None means equal to the discount rate
    debt: float
    cash: float
    shares: float
    mid_year: bool = False


@dataclass(frozen=True)
class RimAssumptions:
    book_value: float
    roe: float
    long_run_roe: float | None  # None means equal to the cost of equity (no excess return)
    payout_ratio: float
    cost_of_equity: float
    terminal_growth: float
    shares: float


@dataclass(frozen=True)
class DdmAssumptions:
    dividend_per_share: float
    growth: float
    terminal_growth: float
    cost_of_equity: float


@dataclass(frozen=True)
class YearRow:
    year: int
    revenue: float | None
    growth: float | None
    margin: float | None
    operating_income: float | None
    nopat: float | None
    reinvestment: float | None
    cash_flow: float  # FCFF, residual income, or dividend
    discount_factor: float
    present_value: float


@dataclass(frozen=True)
class Result:
    model: Model
    per_share: float | None
    enterprise_value: float | None
    equity_value: float | None
    pv_explicit: float
    pv_terminal: float
    terminal_value: float
    terminal_share: float  # PV of terminal ÷ total value
    discount_rate: float
    terminal_growth: float
    years: list[YearRow] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fade(start: float, end: float, year: int) -> float:
    """Value for `year` (1-10): `start` through year 5, then linear to `end` by year 10."""
    if year <= STAGE_ONE:
        return start
    step = (year - STAGE_ONE) / (YEARS - STAGE_ONE)
    return start + (end - start) * step


def _check_rates(discount_rate: float, terminal_growth: float, what: str) -> None:
    if discount_rate - terminal_growth < MIN_SPREAD:
        raise ValuationError(
            f"The {what} ({discount_rate:.2%}) must exceed terminal growth "
            f"({terminal_growth:.2%}) by at least {MIN_SPREAD:.1%}."
        )
    if not -0.05 <= terminal_growth <= 0.06:
        raise ValuationError("Terminal growth must be between −5% and 6%.")


def _factor(rate: float, year: int, mid_year: bool) -> float:
    return 1 / (1 + rate) ** (year - 0.5 if mid_year else year)


def dcf(a: DcfAssumptions) -> Result:
    _check_rates(a.discount_rate, a.terminal_growth, "discount rate")
    if a.base_revenue <= 0:
        raise ValuationError("A DCF needs positive revenue.")
    if a.sales_to_capital <= 0:
        raise ValuationError("Sales-to-capital must be positive.")
    if not 0 <= a.tax_rate < 1:
        raise ValuationError("The tax rate must be between 0% and 100%.")
    if a.shares <= 0:
        raise ValuationError("The share count must be positive.")
    ronic = a.terminal_roic if a.terminal_roic is not None else a.discount_rate
    if ronic <= 0:
        raise ValuationError("Return on new capital must be positive.")

    rows: list[YearRow] = []
    revenue = a.base_revenue
    pv_explicit = 0.0
    for year in range(1, YEARS + 1):
        growth = _fade(a.growth, a.terminal_growth, year)
        margin = _fade(a.margin, a.long_run_margin, year)
        previous, revenue = revenue, revenue * (1 + growth)
        operating_income = revenue * margin
        nopat = operating_income * (1 - a.tax_rate) if operating_income > 0 else operating_income
        reinvestment = (revenue - previous) / a.sales_to_capital
        cash_flow = nopat - reinvestment
        factor = _factor(a.discount_rate, year, a.mid_year)
        pv_explicit += cash_flow * factor
        rows.append(
            YearRow(year, revenue, growth, margin, operating_income, nopat, reinvestment,
                    cash_flow, factor, cash_flow * factor)
        )  # fmt: skip

    terminal_revenue = revenue * (1 + a.terminal_growth)
    terminal_nopat = terminal_revenue * a.long_run_margin * (1 - a.tax_rate)
    terminal_cash_flow = terminal_nopat * (1 - a.terminal_growth / ronic)
    terminal_value = terminal_cash_flow / (a.discount_rate - a.terminal_growth)
    pv_terminal = terminal_value * rows[-1].discount_factor
    enterprise = pv_explicit + pv_terminal
    equity = enterprise - a.debt + a.cash
    return Result(
        model="dcf",
        per_share=equity / a.shares,
        enterprise_value=enterprise,
        equity_value=equity,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        terminal_value=terminal_value,
        terminal_share=pv_terminal / enterprise if enterprise else 0.0,
        discount_rate=a.discount_rate,
        terminal_growth=a.terminal_growth,
        years=rows,
    )


def rim(a: RimAssumptions) -> Result:
    _check_rates(a.cost_of_equity, a.terminal_growth, "cost of equity")
    if a.book_value <= 0:
        raise ValuationError("Residual income needs positive book value.")
    if not 0 <= a.payout_ratio <= 1:
        raise ValuationError("The payout ratio must be between 0% and 100%.")
    if a.shares <= 0:
        raise ValuationError("The share count must be positive.")
    long_run = a.long_run_roe if a.long_run_roe is not None else a.cost_of_equity

    rows: list[YearRow] = []
    book = a.book_value
    pv_explicit = 0.0
    for year in range(1, YEARS + 1):
        roe = _fade(a.roe, long_run, year)
        residual = (roe - a.cost_of_equity) * book
        factor = _factor(a.cost_of_equity, year, False)
        pv_explicit += residual * factor
        rows.append(
            YearRow(year, None, None, roe, None, roe * book, None, residual, factor,
                    residual * factor)
        )  # fmt: skip
        book *= 1 + roe * (1 - a.payout_ratio)
    terminal_residual = (long_run - a.cost_of_equity) * book
    terminal_value = terminal_residual / (a.cost_of_equity - a.terminal_growth)
    pv_terminal = terminal_value * rows[-1].discount_factor
    equity = a.book_value + pv_explicit + pv_terminal
    return Result(
        model="rim",
        per_share=equity / a.shares,
        enterprise_value=None,
        equity_value=equity,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        terminal_value=terminal_value,
        terminal_share=pv_terminal / equity if equity else 0.0,
        discount_rate=a.cost_of_equity,
        terminal_growth=a.terminal_growth,
        years=rows,
    )


def ddm(a: DdmAssumptions) -> Result:
    _check_rates(a.cost_of_equity, a.terminal_growth, "cost of equity")
    if a.dividend_per_share <= 0:
        raise ValuationError("A dividend discount model needs a positive dividend.")
    rows: list[YearRow] = []
    dividend = a.dividend_per_share
    pv_explicit = 0.0
    for year in range(1, YEARS + 1):
        growth = _fade(a.growth, a.terminal_growth, year)
        dividend *= 1 + growth
        factor = _factor(a.cost_of_equity, year, False)
        pv_explicit += dividend * factor
        rows.append(
            YearRow(year, None, growth, None, None, None, None, dividend, factor,
                    dividend * factor)
        )  # fmt: skip
    terminal_value = dividend * (1 + a.terminal_growth) / (a.cost_of_equity - a.terminal_growth)
    pv_terminal = terminal_value * rows[-1].discount_factor
    value = pv_explicit + pv_terminal
    return Result(
        model="ddm",
        per_share=value,
        enterprise_value=None,
        equity_value=None,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        terminal_value=terminal_value,
        terminal_share=pv_terminal / value if value else 0.0,
        discount_rate=a.cost_of_equity,
        terminal_growth=a.terminal_growth,
        years=rows,
    )


Assumptions = DcfAssumptions | RimAssumptions | DdmAssumptions


def apply_scenario(a: Assumptions, s: Scenario) -> Assumptions:
    """Shift the drivers each model has: growth, margin (or ROE), discount rate, terminal growth."""
    if isinstance(a, DcfAssumptions):
        return replace(
            a,
            growth=a.growth + s.growth,
            margin=a.margin + s.margin,
            long_run_margin=a.long_run_margin + s.margin,
            discount_rate=a.discount_rate + s.discount_rate,
            terminal_growth=a.terminal_growth + s.terminal_growth,
        )
    if isinstance(a, RimAssumptions):
        return replace(
            a,
            roe=a.roe + s.margin,
            long_run_roe=None if a.long_run_roe is None else a.long_run_roe + s.margin,
            cost_of_equity=a.cost_of_equity + s.discount_rate,
            terminal_growth=a.terminal_growth + s.terminal_growth,
        )
    return replace(
        a,
        growth=a.growth + s.growth,
        cost_of_equity=a.cost_of_equity + s.discount_rate,
        terminal_growth=a.terminal_growth + s.terminal_growth,
    )


def value(a: Assumptions) -> Result:
    if isinstance(a, DcfAssumptions):
        return dcf(a)
    if isinstance(a, RimAssumptions):
        return rim(a)
    return ddm(a)


@dataclass(frozen=True)
class Grid:
    row_label: str
    column_label: str
    rows: list[float]  # absolute row-driver values
    columns: list[float]
    values: list[list[float | None]]  # per share; None where the model is undefined


def _grid(a: Assumptions, row: tuple[str, list[float]], column: tuple[str, list[float]]) -> Grid:
    def shifted(r: float, c: float) -> float | None:
        scenario = Scenario(**{row[0]: r, column[0]: c})
        try:
            return value(apply_scenario(a, scenario)).per_share
        except ValuationError:
            return None

    base = value(a)
    anchors = {
        "discount_rate": base.discount_rate,
        "terminal_growth": base.terminal_growth,
        "growth": getattr(a, "growth", 0.0),
        "margin": getattr(a, "margin", getattr(a, "roe", 0.0)),
    }
    return Grid(
        row_label=row[0],
        column_label=column[0],
        rows=[anchors[row[0]] + r for r in row[1]],
        columns=[anchors[column[0]] + c for c in column[1]],
        values=[[shifted(r, c) for c in column[1]] for r in row[1]],
    )


def sensitivity(a: Assumptions) -> list[Grid]:
    """Per-share value across discount rate × terminal growth, and growth × margin (or ROE)."""
    steps = [-0.01, -0.005, 0.0, 0.005, 0.01]
    grids = [_grid(a, ("discount_rate", steps), ("terminal_growth", steps))]
    if isinstance(a, DcfAssumptions):
        wide = [-0.04, -0.02, 0.0, 0.02, 0.04]
        grids.append(_grid(a, ("growth", wide), ("margin", wide)))
    elif isinstance(a, RimAssumptions):
        grids.append(
            _grid(a, ("margin", [-0.04, -0.02, 0.0, 0.02, 0.04]), ("discount_rate", steps))
        )
    return grids
