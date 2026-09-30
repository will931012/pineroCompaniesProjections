"""Latest screenable metrics for a company.

The "latest basis" is trailing twelve months (TTM) when the last four fiscal quarters are
available and contiguous, otherwise the latest fiscal year. TTM flows are sums of the four
quarters; balances are taken at the latest quarter end. Market-based metrics combine the
latest stored close with the latest cover-page share count, and state both dates.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from app.analytics import fundamentals as f
from app.fundamentals.concepts import LINE_ITEMS
from app.fundamentals.metrics import METRICS_BY_KEY, Context
from app.fundamentals.statements import (
    Cell,
    FactLike,
    Period,
    StatementSet,
    build_statements,
    shares_for_market_cap,
)

ScreenUnit = Literal["percent", "ratio", "currency", "multiple"]


@dataclass(frozen=True)
class ScreenMetric:
    key: str
    label: str
    category: str
    unit: ScreenUnit
    description: str
    price_based: bool = False


SCREEN_METRICS: tuple[ScreenMetric, ...] = (
    ScreenMetric(
        "market_cap",
        "Market cap",
        "Size",
        "currency",
        "Latest stored close × shares outstanding (cover page, else balance sheet, else latest "
        "weighted-average basic), at most 400 days old",
        True,
    ),
    ScreenMetric(
        "enterprise_value",
        "Enterprise value",
        "Size",
        "currency",
        "Market cap + total debt − cash",
        True,
    ),
    ScreenMetric("revenue", "Revenue (TTM/FY)", "Size", "currency", "Latest-basis revenue"),
    ScreenMetric(
        "revenue_growth_yoy",
        "Revenue growth",
        "Growth",
        "percent",
        "Latest-basis revenue vs the same span a year earlier",
    ),
    ScreenMetric(
        "revenue_cagr_3y",
        "Revenue CAGR (3Y)",
        "Growth",
        "percent",
        "Latest fiscal year vs three years earlier",
    ),
    ScreenMetric(
        "eps_growth_yoy",
        "EPS growth (FY)",
        "Growth",
        "percent",
        "Latest fiscal-year diluted EPS vs prior fiscal year",
    ),
    ScreenMetric("gross_margin", "Gross margin", "Profitability", "percent", "Latest basis"),
    ScreenMetric(
        "operating_margin",
        "Operating margin",
        "Profitability",
        "percent",
        "Latest basis",
    ),
    ScreenMetric("net_margin", "Net margin", "Profitability", "percent", "Latest basis"),
    ScreenMetric("fcf_margin", "FCF margin", "Profitability", "percent", "Latest basis"),
    ScreenMetric("roe", "ROE", "Returns", "percent", "Latest basis, average equity"),
    ScreenMetric("roa", "ROA", "Returns", "percent", "Latest basis, average assets"),
    ScreenMetric(
        "roic",
        "ROIC",
        "Returns",
        "percent",
        "NOPAT ÷ average invested capital, latest basis",
    ),
    ScreenMetric("current_ratio", "Current ratio", "Liquidity", "ratio", "Latest balance sheet"),
    ScreenMetric("debt_to_equity", "Debt / equity", "Leverage", "ratio", "Latest balance sheet"),
    ScreenMetric(
        "debt_to_ebitda",
        "Debt / EBITDA",
        "Leverage",
        "ratio",
        "Latest debt ÷ latest-basis EBITDA",
    ),
    ScreenMetric(
        "net_debt_to_ebitda",
        "Net debt / EBITDA",
        "Leverage",
        "ratio",
        "(Debt − cash) ÷ latest-basis EBITDA",
    ),
    ScreenMetric("interest_coverage", "Interest coverage", "Leverage", "ratio", "Latest basis"),
    ScreenMetric(
        "pe_ratio",
        "P/E",
        "Valuation",
        "multiple",
        "Market cap ÷ latest-basis net income (positive only)",
        True,
    ),
    ScreenMetric(
        "price_to_sales",
        "P/S",
        "Valuation",
        "multiple",
        "Market cap ÷ latest-basis revenue",
        True,
    ),
    ScreenMetric(
        "price_to_book",
        "P/B",
        "Valuation",
        "multiple",
        "Market cap ÷ shareholders' equity (positive only)",
        True,
    ),
    ScreenMetric(
        "ev_to_ebitda",
        "EV / EBITDA",
        "Valuation",
        "multiple",
        "Enterprise value ÷ latest-basis EBITDA (positive only)",
        True,
    ),
    ScreenMetric(
        "fcf_yield",
        "FCF yield",
        "Valuation",
        "percent",
        "Latest-basis free cash flow ÷ market cap",
        True,
    ),
    ScreenMetric(
        "dividend_yield",
        "Dividend yield",
        "Shareholders",
        "percent",
        "Latest-basis dividends paid ÷ market cap",
        True,
    ),
    ScreenMetric(
        "buyback_yield",
        "Buyback yield",
        "Shareholders",
        "percent",
        "Latest-basis share repurchases ÷ market cap",
        True,
    ),
    ScreenMetric(
        "momentum_1m",
        "Momentum 1M",
        "Momentum",
        "percent",
        "Adjusted close vs one month earlier",
        True,
    ),
    ScreenMetric(
        "momentum_3m",
        "Momentum 3M",
        "Momentum",
        "percent",
        "Adjusted close vs three months earlier",
        True,
    ),
    ScreenMetric(
        "momentum_6m",
        "Momentum 6M",
        "Momentum",
        "percent",
        "Adjusted close vs six months earlier",
        True,
    ),
    ScreenMetric(
        "momentum_12m",
        "Momentum 12M",
        "Momentum",
        "percent",
        "Adjusted close vs twelve months earlier",
        True,
    ),
    ScreenMetric(
        "volatility_1y",
        "Volatility (1Y)",
        "Risk",
        "percent",
        "Annualised std. dev. of daily log returns, last 252 sessions",
        True,
    ),
)
SCREEN_METRICS_BY_KEY = {metric.key: metric for metric in SCREEN_METRICS}


@dataclass(frozen=True)
class PricePoint:
    trade_date: date
    close: Decimal
    adj_close: Decimal | None


@dataclass(frozen=True)
class MetricValue:
    key: str
    value: Decimal
    basis: str
    period_end: date | None
    available_date: date | None
    price_based: bool = False


@dataclass(frozen=True)
class LatestBasis:
    statements: StatementSet  # two synthetic periods: previous span and latest span
    label: str
    end: date
    available_date: date


def _contiguous(periods: Sequence[Period]) -> bool:
    for before, after in pairwise(periods):
        expected = (
            (before.fiscal_year, (before.fiscal_quarter or 0) + 1)
            if before.fiscal_quarter != 4
            else (before.fiscal_year + 1, 1)
        )
        if (after.fiscal_year, after.fiscal_quarter) != expected:
            return False
    return True


def _span(
    source: StatementSet, periods: Sequence[Period], fiscal_year: int, key: str
) -> tuple[Period, dict[str, Cell]]:
    """Collapse periods into one: flows summed (all present), balances at the last period."""
    last = periods[-1]
    cells: dict[str, Cell] = {}
    for item in LINE_ITEMS:
        if item.kind == "instant":
            cell = source.cells.get(item.key, {}).get(last.key)
            if cell:
                cells[item.key] = cell
        elif item.kind == "flow" or len(periods) == 1:
            parts = [source.cells.get(item.key, {}).get(p.key) for p in periods]
            if all(parts):
                present = [p for p in parts if p is not None]
                cells[item.key] = Cell(
                    sum((p.value for p in present), Decimal(0)),
                    None,
                    None,
                    max(p.filed_date for p in present),
                    None if len(periods) == 1 else "Sum of four quarters",
                )
    return Period(key, key, fiscal_year, None, periods[0].start, last.end), cells


def _synthetic(
    source: StatementSet, groups: list[list[Period]], label: str, last: Period
) -> LatestBasis:
    synthetic = StatementSet("annual", [])
    for offset, group in enumerate(groups):
        fiscal_year = 1000 + offset + (2 - len(groups))
        period, cells = _span(source, group, fiscal_year, f"span{fiscal_year}")
        synthetic.periods.append(period)
        for item_key, cell in cells.items():
            synthetic.cells.setdefault(item_key, {})[period.key] = cell
    latest_key = synthetic.periods[-1].key
    filed = [
        cells[latest_key].filed_date for cells in synthetic.cells.values() if latest_key in cells
    ]
    return LatestBasis(synthetic, label, last.end, max(filed) if filed else last.end)


def ttm_basis(quarterly: StatementSet) -> LatestBasis | None:
    """Last four contiguous quarters (and the four before them, for growth)."""
    q = quarterly.periods
    if len(q) < 4 or not _contiguous(q[-4:]):
        return None
    groups = [q[-8:-4], q[-4:]] if len(q) >= 8 and _contiguous(q[-8:]) else [q[-4:]]
    return _synthetic(quarterly, groups, f"TTM {q[-1].label}", q[-1])


def fy_basis(annual: StatementSet) -> LatestBasis | None:
    """Latest fiscal year (and the prior year when adjacent, for growth and averages)."""
    if not annual.periods:
        return None
    last = annual.periods[-1]
    recent = annual.periods[-2:]
    adjacent = len(recent) == 2 and recent[0].fiscal_year == last.fiscal_year - 1
    return _synthetic(annual, [[p] for p in recent] if adjacent else [[last]], last.label, last)


def _momentum(prices: Sequence[PricePoint], months: int) -> Decimal | None:
    if not prices:
        return None
    last = prices[-1]
    target = date.fromordinal(last.trade_date.toordinal() - round(months * 30.4375))
    earlier = [p for p in prices if p.trade_date <= target]
    if not earlier or (target - earlier[-1].trade_date).days > 7:
        return None
    base = earlier[-1].adj_close or earlier[-1].close
    now = last.adj_close or last.close
    return f.growth(now, base)


def _volatility(prices: Sequence[PricePoint]) -> Decimal | None:
    closes = [float(p.adj_close or p.close) for p in prices[-253:]]
    returns = [math.log(b / a) for a, b in pairwise(closes) if a > 0 and b > 0]
    if len(returns) < 200:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return Decimal(str(math.sqrt(variance) * math.sqrt(252)))


FUNDAMENTAL_KEYS = (
    "revenue_growth_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "roa",
    "roic",
    "current_ratio",
    "debt_to_equity",
    "debt_to_ebitda",
    "net_debt_to_ebitda",
    "interest_coverage",
)


def compute_company_metrics(
    facts: Sequence[FactLike], prices: Sequence[PricePoint], as_of: date | None = None
) -> list[MetricValue]:
    """Each metric uses TTM when its inputs exist for four quarters, else the latest fiscal
    year; the basis actually used is recorded with the value."""

    annual = build_statements(facts, "annual", as_of)
    quarterly = build_statements(facts, "quarterly", as_of)
    bases = [
        (b, Context(b.statements, len(b.statements.periods) - 1))
        for b in (ttm_basis(quarterly), fy_basis(annual))
        if b is not None
    ]
    results: list[MetricValue] = []

    def first(compute: Callable[[Context], Decimal | None]) -> tuple[Decimal, LatestBasis] | None:
        for basis, ctx in bases:
            value = compute(ctx)
            if value is not None:
                return value, basis
        return None

    def add_fundamental(key: str, compute: Callable[[Context], Decimal | None]) -> None:
        found = first(compute)
        if found is not None:
            value, basis = found
            results.append(MetricValue(key, value, basis.label, basis.end, basis.available_date))

    add_fundamental("revenue", lambda c: c.cur("revenue"))
    for key in FUNDAMENTAL_KEYS:
        add_fundamental(key, METRICS_BY_KEY[key].compute)

    if annual.periods:
        fy_ctx = Context(annual, len(annual.periods) - 1)
        fy = annual.periods[-1]
        fy_filed = max(
            (c[fy.key].filed_date for c in annual.cells.values() if fy.key in c), default=None
        )
        for key in ("revenue_cagr_3y", "eps_growth_yoy"):
            value = METRICS_BY_KEY[key].compute(fy_ctx)
            if value is not None:
                results.append(MetricValue(key, value, fy.label, fy.end, fy_filed))

    usable_prices = [p for p in prices if as_of is None or p.trade_date <= as_of]
    if not usable_prices:
        return results
    last_price = usable_prices[-1]
    price_label = f"Close {last_price.trade_date.isoformat()}"
    for key, value in [
        *((f"momentum_{m}m", _momentum(usable_prices, m)) for m in (1, 3, 6, 12)),
        ("volatility_1y", _volatility(usable_prices)),
    ]:
        if value is not None:
            results.append(
                MetricValue(
                    key, value, price_label, last_price.trade_date, last_price.trade_date, True
                )
            )

    shares = shares_for_market_cap(facts, as_of, last_price.trade_date)
    if shares is None:
        return results
    market_cap = last_price.close * shares.value
    classes = f", {shares.classes} share classes" if shares.classes > 1 else ""
    cap_label = f"{price_label} × {shares.source} shares {shares.as_of.isoformat()}{classes}"
    cap_available = max(last_price.trade_date, shares.filed_date)
    results.append(
        MetricValue("market_cap", market_cap, cap_label, last_price.trade_date, cap_available, True)
    )

    def enterprise_value(c: Context) -> Decimal | None:
        debt, cash = c.debt(), c.cur("cash")
        return market_cap + debt - cash if debt is not None and cash is not None else None

    market_formulas: dict[str, Callable[[Context], Decimal | None]] = {
        "enterprise_value": enterprise_value,
        "pe_ratio": lambda c: f.earnings_multiple(market_cap, c.cur("net_income")),
        "price_to_sales": lambda c: f.earnings_multiple(market_cap, c.cur("revenue")),
        "price_to_book": lambda c: f.earnings_multiple(market_cap, c.cur("total_equity")),
        "ev_to_ebitda": lambda c: f.earnings_multiple(enterprise_value(c), c.ebitda()),
        "fcf_yield": lambda c: f.yield_on(c.fcf(), market_cap),
        "dividend_yield": lambda c: f.yield_on(c.cur("dividends_paid"), market_cap),
        "buyback_yield": lambda c: f.yield_on(c.cur("buybacks"), market_cap),
    }
    for key, compute in market_formulas.items():
        found = first(compute)
        if found is not None:
            value, basis = found
            results.append(
                MetricValue(
                    key,
                    value,
                    f"{cap_label}; {basis.label}",
                    last_price.trade_date,
                    max(cap_available, basis.available_date),
                    True,
                )
            )
    return results
