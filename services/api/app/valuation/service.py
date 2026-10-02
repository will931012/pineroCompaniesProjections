"""Default assumptions for each valuation model, built from stored data with their sources.

Every default records where it came from. When an input is missing, a stated fallback is
used and a warning says so; nothing is silently invented.
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics import fundamentals as f
from app.analytics.valuation import Model
from app.db.base import utcnow
from app.db.models import Company, CompanyMetric, MarketRate, ProviderFetch, Security
from app.fundamentals.metrics import Context
from app.fundamentals.service import load_facts, load_prices
from app.fundamentals.snapshot import PricePoint, fy_basis, ttm_basis
from app.fundamentals.statements import StatementSet, build_statements, shares_for_market_cap
from app.providers.base import ProviderError, record_fetch
from app.providers.treasury import TreasuryClient

logger = logging.getLogger(__name__)

RATE_SERIES = "treasury_par"
RISK_FREE_TENOR = "10 Yr"
RATE_TTL = timedelta(hours=12)
EQUITY_RISK_PREMIUM = 0.05
DEFAULT_TERMINAL_GROWTH = 0.025
STATUTORY_TAX = 0.21
BENCHMARK = "SPY"
# SIC 6000-6799: depository institutions, credit, brokers, insurance, real estate, holding cos.
FINANCIAL_SIC = range(6000, 6800)


@dataclass
class Defaults:
    recommended: Model
    available: list[Model]
    unavailable: dict[str, str]
    # model -> field -> value; "capm" holds cost-of-capital inputs shared by all models.
    inputs: dict[str, dict[str, Any]]
    capm: dict[str, float]
    sources: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    price: float | None = None
    price_date: date | None = None
    basis_label: str | None = None


# --- Risk-free rate -----------------------------------------------------------------------


def latest_risk_free(db: Session, client: TreasuryClient | None) -> tuple[float, date, int] | None:
    """(rate, observed on, fetch id) of the latest 10-year par yield; refreshed when stale."""

    def latest() -> MarketRate | None:
        return db.scalar(
            select(MarketRate)
            .where(MarketRate.series == RATE_SERIES, MarketRate.tenor == RISK_FREE_TENOR)
            .order_by(MarketRate.observed_on.desc())
            .limit(1)
        )

    row = latest()
    fetched = db.get(ProviderFetch, row.fetch_id) if row else None
    stale = fetched is None or (
        fetched.retrieved_at is not None and utcnow() - fetched.retrieved_at > RATE_TTL
    )
    if client is not None and stale:
        try:
            result = client.yield_curve(utcnow().year)
        except ProviderError as error:
            if error.meta is not None:
                record_fetch(db, error.meta, status="error", error=error)
                db.commit()
        else:
            fetch = record_fetch(
                db,
                result.meta,
                status="success",
                record_count=len(result.data),
                rejected_count=result.rejected_count,
            )
            values = [
                {
                    "series": RATE_SERIES,
                    "tenor": o.tenor,
                    "observed_on": o.observed_on,
                    "value": Decimal(str(round(o.value, 8))),
                    "fetch_id": fetch.id,
                }
                for o in result.data
            ]
            db.execute(insert(MarketRate).values(values).on_conflict_do_nothing())
            db.commit()
            row = latest()
    return (float(row.value), row.observed_on, row.fetch_id) if row else None


# --- Beta ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Beta:
    raw: float
    adjusted: float
    observations: int
    start: date
    end: date


def estimate_beta(
    stock: list[PricePoint], market: list[PricePoint], *, min_weeks: int = 52
) -> Beta | None:
    """Weekly log-return beta over up to two years, Blume-adjusted (0.67 × raw + 0.33).

    Weekly returns (every fifth shared trading day) damp the noise of daily, non-synchronous
    trading. Adjusted (not raw) beta is the default because raw betas regress toward 1."""
    market_close = {p.trade_date: float(p.adj_close or p.close) for p in market}
    shared = [
        (p.trade_date, float(p.adj_close or p.close), market_close[p.trade_date])
        for p in stock
        if p.trade_date in market_close
    ][-505:]
    weekly = shared[::5]
    pairs = [
        (math.log(b[1] / a[1]), math.log(b[2] / a[2]))
        for a, b in pairwise(weekly)
        if a[1] > 0 and b[1] > 0 and a[2] > 0 and b[2] > 0
    ]
    if len(pairs) < min_weeks:
        return None
    mean_s = sum(s for s, _ in pairs) / len(pairs)
    mean_m = sum(m for _, m in pairs) / len(pairs)
    cov = sum((s - mean_s) * (m - mean_m) for s, m in pairs) / (len(pairs) - 1)
    var = sum((m - mean_m) ** 2 for _, m in pairs) / (len(pairs) - 1)
    if var <= 0:
        return None
    raw = cov / var
    return Beta(raw, 0.67 * raw + 0.33, len(pairs), weekly[0][0], weekly[-1][0])


# --- Defaults -----------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class Latest:
    """The latest figures: trailing twelve months first, else the latest fiscal year.

    Some items are reported only annually (or only in some quarters), so each lookup falls
    back separately and returns the basis it actually used."""

    def __init__(self, annual: StatementSet, quarterly: StatementSet) -> None:
        self.bases = [
            (b.label, Context(b.statements, len(b.statements.periods) - 1))
            for b in (ttm_basis(quarterly), fy_basis(annual))
            if b is not None
        ]

    @property
    def label(self) -> str | None:
        return self.bases[0][0] if self.bases else None

    def value(self, compute: Callable[[Context], Decimal | None]) -> tuple[float | None, str]:
        for label, ctx in self.bases:
            found = compute(ctx)
            if found is not None:
                return float(found), label
        return None, self.label or "no data"

    def item(self, key: str) -> tuple[float | None, str]:
        return self.value(lambda c: c.cur(key))

    def find(self, *keys: str) -> tuple[Context, str] | None:
        """The first basis reporting every key, so ratios use figures from one period."""
        for label, ctx in self.bases:
            if all(ctx.cur(k) is not None for k in keys):
                return ctx, label
        return None


def _sum_years(annual: StatementSet, item: str, years: int) -> tuple[float | None, list[str]]:
    keys = [p.key for p in annual.periods[-years:]]
    values = [annual.get(item, k) for k in keys]
    present = [(k, v) for k, v in zip(keys, values, strict=True) if v is not None]
    if len(present) < len(keys) or not present:
        return None, []
    return float(sum((v for _, v in present), Decimal(0))), [k for k, _ in present]


def _cagr(annual: StatementSet, item: str, years: int) -> tuple[float | None, str | None]:
    periods = annual.periods
    if len(periods) <= years:
        return None, None
    end, start = annual.get(item, periods[-1].key), annual.get(item, periods[-1 - years].key)
    if end is None or start is None or start <= 0 or end <= 0:
        return None, None
    rate = float((end / start) ** (Decimal(1) / years)) - 1
    return rate, f"{years}-year CAGR {periods[-1 - years].label}–{periods[-1].label}"


def build_defaults(db: Session, company: Company, treasury: TreasuryClient | None) -> Defaults:
    facts = load_facts(db, company)
    annual = build_statements(facts, "annual")
    quarterly = build_statements(facts, "quarterly")
    latest = Latest(annual, quarterly)
    sources: dict[str, str] = {}
    warnings: list[str] = []
    unavailable: dict[str, str] = {}
    if latest.label is None:
        return Defaults("dcf", [], {"dcf": "No SEC financial data is stored for this company."},
                        {}, {}, {}, ["Load the company's Financials first."])  # fmt: skip

    # Market data: latest stored close and the matching share count.
    prices = load_prices(db, company)
    price = float(prices[-1].close) if prices else None
    price_date = prices[-1].trade_date if prices else None
    # Without a price, the count only has to be current for the latest reported period.
    period_ends = [p.end for p in (*annual.periods, *quarterly.periods)]
    reference = price_date or max(period_ends, default=utcnow().date())
    share_count = shares_for_market_cap(facts, None, reference)
    shares = float(share_count.value) if share_count else None
    if share_count:
        sources["shares"] = f"{share_count.source} shares {share_count.as_of.isoformat()}"
    else:
        shares, shares_basis = latest.item("shares_diluted")
        if shares is not None:
            sources["shares"] = f"weighted diluted shares, {shares_basis}"
            warnings.append(
                "No recent shares-outstanding figure; weighted diluted shares are used."
            )

    # Risk-free rate.
    rate = latest_risk_free(db, treasury)
    if rate:
        risk_free = rate[0]
        sources["risk_free"] = f"US Treasury {RISK_FREE_TENOR} par yield {rate[1].isoformat()}"
    else:
        risk_free = 0.045
        sources["risk_free"] = "assumed 4.5% (Treasury rate unavailable)"
        warnings.append("The Treasury yield could not be loaded; a 4.5% risk-free rate is assumed.")

    # Beta against the S&P 500 (SPY), when both price histories are stored.
    beta_value = 1.0
    benchmark = db.scalar(select(Security).where(Security.ticker == BENCHMARK, Security.is_active))
    estimate = (
        estimate_beta(prices, load_prices(db, benchmark.company)) if benchmark and prices else None
    )
    if estimate:
        beta_value = round(_clamp(estimate.adjusted, 0.3, 3.0), 3)
        sources["beta"] = (
            f"weekly returns vs {BENCHMARK}, {estimate.start}–{estimate.end} "
            f"({estimate.observations} weeks), raw {estimate.raw:.2f}, Blume-adjusted"
        )
    else:
        sources["beta"] = "assumed 1.0 (no stored price history for the company and SPY)"
        warnings.append(
            "Beta is assumed to be 1.0: price history for the company and SPY is needed."
        )

    # Tax: three-year effective rate, bounded; statutory rate when not computable.
    tax_paid, tax_years = _sum_years(annual, "income_tax", 3)
    pretax, _ = _sum_years(annual, "pretax_income", 3)
    if tax_paid is not None and pretax and pretax > 0:
        tax_rate = _clamp(tax_paid / pretax, 0.10, 0.30)
        sources["tax_rate"] = f"effective rate {tax_years[0]}–{tax_years[-1]}, bounded to 10–30%"
    else:
        tax_rate = STATUTORY_TAX
        sources["tax_rate"] = "US federal statutory 21% (effective rate not computable)"

    found_debt, debt_basis = latest.value(lambda c: c.debt())
    debt = found_debt or 0.0
    found_cash, cash_basis = latest.value(
        lambda c: f.total(c.cur("cash"), c.cur("short_term_investments"))
    )
    cash = found_cash or 0.0
    sources["debt"] = (
        f"short- plus long-term debt, {debt_basis}"
        if found_debt is not None
        else "not reported in SEC's standard debt tags; 0 assumed"
    )
    sources["cash"] = (
        f"cash and short-term investments, {cash_basis}"
        if found_cash is not None
        else "not reported in SEC's standard cash tags; 0 assumed"
    )
    for label, found in (("Debt", found_debt), ("Cash", found_cash)):
        if found is None:
            warnings.append(
                f"{label} is not reported under SEC's standard tags, so 0 is assumed; "
                "enter the figure if you know it."
            )

    # Cost of debt: interest expense over debt, bounded around the risk-free rate.
    interest, interest_basis = latest.item("interest_expense")
    if interest and debt > 0:
        cost_of_debt = _clamp(interest / debt, risk_free, risk_free + 0.08)
        sources["pre_tax_cost_of_debt"] = (
            f"interest expense ({interest_basis}) ÷ debt, bounded to rf…rf+8%"
        )
    else:
        cost_of_debt = risk_free + 0.015
        sources["pre_tax_cost_of_debt"] = "risk-free + 1.5% (interest expense not reported)"

    market_cap = db.scalar(
        select(CompanyMetric.value).where(
            CompanyMetric.company_id == company.id, CompanyMetric.metric == "market_cap"
        )
    )
    book_equity, equity_basis = latest.item("total_equity")
    if market_cap is not None:
        equity_value = float(market_cap)
        sources["equity_value"] = "market capitalisation (latest close × shares)"
    else:
        equity_value = max(book_equity or 0.0, 0.0)
        sources["equity_value"] = f"book equity, {equity_basis} (no market price stored)"
        warnings.append("Capital weights use book equity because no market price is stored.")

    capm = {
        "risk_free": round(risk_free, 6),
        "beta": beta_value,
        "equity_risk_premium": EQUITY_RISK_PREMIUM,
        "pre_tax_cost_of_debt": round(cost_of_debt, 6),
        "equity_value": equity_value,
        "debt_value": debt,
    }
    sources["equity_risk_premium"] = "default 5% (editable)"
    sources["debt_value"] = sources["debt"]
    terminal_growth = min(DEFAULT_TERMINAL_GROWTH, risk_free)
    sources["terminal_growth"] = "default 2.5%, capped at the risk-free rate"

    inputs: dict[str, dict[str, Any]] = {}
    available: list[Model] = []

    # DCF
    operating = latest.find("revenue", "operating_income")
    revenue = _f(operating[0].cur("revenue")) if operating else None
    if shares is None or shares <= 0:
        unavailable["dcf"] = unavailable["rim"] = unavailable["ddm"] = (
            "No current share count is reported under SEC's standard tags (some issuers, such "
            "as multi-class companies, use their own), so value per share cannot be computed."
        )
    elif operating is None or revenue is None or revenue <= 0:
        unavailable["dcf"] = "Revenue and operating income are needed for a DCF."
    else:
        context, label = operating
        operating_income = float(context.cur("operating_income") or 0)
        growth, growth_source = _cagr(annual, "revenue", 3)
        if growth is None:
            year_ago = _f(context.year_ago("revenue"))
            growth = revenue / year_ago - 1 if year_ago and year_ago > 0 else 0.0
            growth_source = f"revenue growth, {label}" if year_ago else "assumed 0%"
        growth = _clamp(growth, -0.10, 0.30)
        margins: list[float] = []
        for period in annual.periods[-5:]:
            period_revenue = annual.get("revenue", period.key)
            period_income = annual.get("operating_income", period.key)
            if period_revenue and period_revenue > 0 and period_income is not None:
                margins.append(float(period_income / period_revenue))
        margin = operating_income / revenue
        long_run = sum(margins) / len(margins) if margins else margin
        invested = (book_equity or 0.0) + debt - cash
        sales_to_capital = _clamp(revenue / invested, 0.5, 5.0) if invested > 0 else 1.5
        inputs["dcf"] = {
            "base_revenue": revenue,
            "growth": round(growth, 4),
            "margin": round(margin, 4),
            "long_run_margin": round(long_run, 4),
            "terminal_growth": terminal_growth,
            "tax_rate": round(tax_rate, 4),
            "sales_to_capital": round(sales_to_capital, 3),
            "terminal_roic": None,
            "debt": debt,
            "cash": cash,
            "shares": shares,
            "mid_year": False,
        }
        sources["base_revenue"] = f"revenue, {label}"
        sources["growth"] = f"{growth_source}, bounded to −10…30%"
        sources["margin"] = f"operating margin, {label}"
        sources["long_run_margin"] = (
            f"average operating margin of the last {len(margins)} fiscal years"
            if margins
            else "current operating margin"
        )
        sources["sales_to_capital"] = (
            f"revenue ({label}) ÷ (equity + debt − cash), bounded to 0.5–5"
            if invested > 0
            else "assumed 1.5 (invested capital not positive)"
        )
        sources["terminal_roic"] = "equal to the discount rate (growth after year 10 adds no value)"
        available.append("dcf")

    # Residual income: net income, equity and dividends from one period.
    earnings = latest.find("net_income", "total_equity")
    if shares and book_equity and book_equity > 0 and earnings is not None:
        context, label = earnings
        net_income = float(context.cur("net_income") or 0)
        period_equity = float(context.cur("total_equity") or 0)
        previous_equity = _f(context.previous("total_equity"))
        average_equity = (period_equity + previous_equity) / 2 if previous_equity else period_equity
        dividends = _f(context.cur("dividends_paid")) or 0.0
        payout = _clamp(dividends / net_income, 0.0, 1.0) if net_income > 0 else 0.0
        roe = net_income / average_equity if average_equity > 0 else 0.0
        inputs["rim"] = {
            "book_value": book_equity,
            "roe": round(_clamp(roe, -0.20, 0.40), 4),
            "long_run_roe": None,
            "payout_ratio": round(payout, 4),
            "terminal_growth": terminal_growth,
            "shares": shares,
        }
        sources["book_value"] = f"shareholders' equity, {equity_basis}"
        sources["roe"] = f"net income ÷ average equity, {label}, bounded to −20…40%"
        sources["long_run_roe"] = "equal to the cost of equity (no excess return after year 10)"
        sources["payout_ratio"] = f"dividends paid ÷ net income, {label}"
        available.append("rim")
    elif "rim" not in unavailable:
        unavailable["rim"] = "Positive book equity and reported net income are needed."

    # Dividend discount
    dps_periods = [p for p in annual.periods if annual.get("dividends_per_share", p.key)]
    if shares and dps_periods:
        last = dps_periods[-1]
        dps = float(annual.get("dividends_per_share", last.key) or 0)
        dividend_growth, dividend_source = _cagr(annual, "dividends_per_share", 5)
        inputs["ddm"] = {
            "dividend_per_share": dps,
            "growth": round(_clamp(dividend_growth or 0.03, -0.05, 0.12), 4),
            "terminal_growth": terminal_growth,
        }
        sources["dividend_per_share"] = f"dividends declared per share, {last.label}"
        sources["dividend_growth"] = (
            f"{dividend_source}, bounded to −5…12%" if dividend_source else "assumed 3%"
        )
        available.append("ddm")
    elif "ddm" not in unavailable:
        unavailable["ddm"] = "The company reports no dividends per share."

    sic = int(company.sic_code) if company.sic_code and company.sic_code.isdigit() else None
    financial = sic is not None and sic in FINANCIAL_SIC
    if financial:
        warnings.append(
            "Financial company (SIC 60–67): cash flow to the firm is not meaningful for banks and "
            "insurers, so residual income is recommended."
        )
    recommended: Model = (
        "rim" if financial and "rim" in available else "dcf" if "dcf" in available
        else available[0] if available else "dcf"
    )  # fmt: skip
    return Defaults(
        recommended=recommended,
        available=available,
        unavailable=unavailable,
        inputs=inputs,
        capm=capm,
        sources=sources,
        warnings=warnings,
        price=price,
        price_date=price_date,
        basis_label=latest.label,
    )
