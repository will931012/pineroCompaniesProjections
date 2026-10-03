"""Point-in-time feature snapshots for the model universe.

`compute_features` is a pure function of a company's inputs and a date. Every input is cut
to what was public on that date before use:
- prices: bars on or before the date (total returns rebuilt from raw prices);
- fundamentals: Phase 2 metrics built only from facts filed on or before the date;
- macro: FRED vintages available by the date.
The leakage tests compute features from full histories and from histories truncated at the
date and require identical results, and every stored snapshot asserts
`data_available_on <= as_of`.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics import technicals as t
from app.db.models import (
    Company,
    FeatureSnapshot,
    FinancialFact,
    Security,
    UniverseMember,
)
from app.fundamentals.snapshot import PricePoint, compute_company_metrics
from app.fundamentals.statements import FactLike
from app.quant.macro import MacroHistory, macro_features
from app.quant.prices import ReturnSeries, return_series
from app.quant.universe import UNIVERSE_VERSION

logger = logging.getLogger(__name__)

FEATURE_SET_VERSION = "f1"
BENCHMARK = "SPY"

PRICE_FEATURES = (
    "mom_1m", "mom_3m", "mom_6m", "mom_12_1", "vol_1m", "vol_3m", "beta_1y",
    "max_drawdown_1y", "dist_sma200", "rsi_14",
)  # fmt: skip
FUNDAMENTAL_FEATURES = (
    "revenue_growth_yoy", "revenue_cagr_3y", "eps_growth_yoy", "gross_margin",
    "operating_margin", "net_margin", "fcf_margin", "roe", "roa", "roic", "current_ratio",
    "debt_to_equity", "interest_coverage",
)  # fmt: skip
VALUE_FEATURES = (
    "earnings_yield", "sales_yield", "book_to_price", "ebitda_to_ev", "fcf_yield",
    "dividend_yield", "buyback_yield", "log_market_cap",
)  # fmt: skip
MARKET_FEATURES = (
    "macro_10y", "macro_curve_10y3m", "macro_unemployment", "macro_nfci", "macro_cpi_yoy",
    "macro_unemployment_change_3m", "macro_10y_change_3m", "market_trend", "market_vol_1m",
)  # fmt: skip
# Features that differ across companies on a date; models see their cross-sectional ranks.
CROSS_SECTIONAL = PRICE_FEATURES + FUNDAMENTAL_FEATURES + VALUE_FEATURES
FEATURE_NAMES = CROSS_SECTIONAL + MARKET_FEATURES


@dataclass(frozen=True)
class CompanyInputs:
    facts: Sequence[FactLike]
    prices: ReturnSeries
    points: Sequence[PricePoint]


@dataclass(frozen=True)
class MarketInputs:
    benchmark: ReturnSeries
    macro: MacroHistory


def _ret(index: list[float], back: int, skip: int = 0) -> float | None:
    end = len(index) - 1 - skip
    start = end - back
    if start < 0 or index[start] <= 0:
        return None
    return index[end] / index[start] - 1


def price_features(series: ReturnSeries, benchmark: ReturnSeries) -> dict[str, float | None]:
    index = series.index
    returns = series.returns
    closes = series.closes
    sma200 = t.sma(closes, 200)[-1] if closes else None
    bench = dict(zip(benchmark.dates[1:], benchmark.returns, strict=True))
    own = list(zip(series.dates[1:], returns, strict=True))[-252:]
    paired = [(r, bench[d]) for d, r in own if d in bench]
    return {
        "mom_1m": _ret(index, 21),
        "mom_3m": _ret(index, 63),
        "mom_6m": _ret(index, 126),
        "mom_12_1": _ret(index, 231, skip=21),
        "vol_1m": t.annualised_volatility(returns[-21:]) if len(returns) >= 21 else None,
        "vol_3m": t.annualised_volatility(returns[-63:]) if len(returns) >= 63 else None,
        "beta_1y": t.beta([a for a, _ in paired], [b for _, b in paired])
        if len(paired) >= 200
        else None,
        "max_drawdown_1y": t.max_drawdown(index[-253:]) if len(index) >= 253 else None,
        "dist_sma200": closes[-1] / sma200 - 1 if sma200 else None,
        "rsi_14": t.rsi(closes[-60:], 14)[-1] if len(closes) >= 60 else None,
    }


def _inverse(value: float | None) -> float | None:
    return 1 / value if value is not None and value != 0 else None


def compute_features(
    company: CompanyInputs, market: MarketInputs, day: date
) -> tuple[dict[str, float | None], date] | None:
    """Features as of `day` and the latest publication date used; None without a recent price."""
    series = company.prices.as_of(day)
    if not series.dates or (day - series.dates[-1]).days > 7 or len(series.dates) < 253:
        return None
    benchmark = market.benchmark.as_of(day)
    used = [series.dates[-1]]
    features: dict[str, float | None] = price_features(series, benchmark)

    metrics = {
        m.key: m
        for m in compute_company_metrics(
            company.facts, [p for p in company.points if p.trade_date <= day], as_of=day
        )
    }

    def metric(key: str) -> float | None:
        found = metrics.get(key)
        if found is None:
            return None
        if found.available_date:
            used.append(found.available_date)
        return float(found.value)

    for key in FUNDAMENTAL_FEATURES:
        features[key] = metric(key)
    features["earnings_yield"] = _inverse(metric("pe_ratio"))
    features["sales_yield"] = _inverse(metric("price_to_sales"))
    features["book_to_price"] = _inverse(metric("price_to_book"))
    features["ebitda_to_ev"] = _inverse(metric("ev_to_ebitda"))
    features["fcf_yield"] = metric("fcf_yield")
    features["dividend_yield"] = metric("dividend_yield")
    features["buyback_yield"] = metric("buyback_yield")
    cap = metric("market_cap")
    features["log_market_cap"] = math.log(cap) if cap and cap > 0 else None

    macro, macro_used = macro_features(market.macro, day)
    features.update(macro)
    if macro_used:
        used.append(macro_used)
    bench_sma = t.sma(benchmark.closes, 200)[-1] if benchmark.closes else None
    features["market_trend"] = benchmark.closes[-1] / bench_sma - 1 if bench_sma else None
    features["market_vol_1m"] = (
        t.annualised_volatility(benchmark.returns[-21:]) if len(benchmark.returns) >= 21 else None
    )
    if benchmark.dates:
        used.append(benchmark.dates[-1])
    clean = {k: (v if v is not None and math.isfinite(v) else None) for k, v in features.items()}
    return clean, max(used)


def load_company_inputs(db: Session, company: Company, security_id: int) -> CompanyInputs:
    facts = db.scalars(select(FinancialFact).where(FinancialFact.company_id == company.id)).all()
    series = return_series(db, security_id)
    points = [
        PricePoint(d, Decimal(str(c)), None)
        for d, c in zip(series.dates, series.closes, strict=True)
    ]
    return CompanyInputs(facts, series, points)


def load_market_inputs(db: Session) -> MarketInputs:
    spy = db.scalar(select(Security).where(Security.ticker == BENCHMARK, Security.is_active))
    benchmark = return_series(db, spy.id) if spy else ReturnSeries([], [], [])
    return MarketInputs(benchmark, MacroHistory(db))


def _company_rows(
    db: Session, market: MarketInputs, company_id: int, security_id: int, days: list[date]
) -> tuple[list[dict[str, object]], int]:
    company = db.get(Company, company_id)
    if company is None:
        return [], 0
    inputs = load_company_inputs(db, company, security_id)
    rows: list[dict[str, object]] = []
    skipped = 0
    for day in sorted(days):
        computed = compute_features(inputs, market, day)
        if computed is None:
            skipped += 1
            continue
        features, available = computed
        if available > day:  # would be look-ahead; never store it
            raise AssertionError(f"Feature input dated {available} used on {day}.")
        rows.append(
            {
                "version": FEATURE_SET_VERSION, "as_of": day, "company_id": company_id,
                "features": features, "data_available_on": available,
            }
        )  # fmt: skip
    return rows, skipped


def _work(batch: list[tuple[int, int, list[date]]]) -> tuple[int, int]:
    """Process-pool worker: its own session and market inputs, then write its rows."""
    from app.db.session import get_sessionmaker

    stored = skipped = 0
    with get_sessionmaker()() as db:
        market = load_market_inputs(db)
        for company_id, security_id, days in batch:
            rows, missed = _company_rows(db, market, company_id, security_id, days)
            if rows:
                db.execute(insert(FeatureSnapshot).values(rows))
                db.commit()
            stored += len(rows)
            skipped += missed
    return stored, skipped


def build_snapshots(
    db: Session, dates: list[date], workers: int = 1, *, rebuild: bool = False
) -> dict[str, object]:
    """Store snapshots for priced universe members on `dates` that do not have one yet.

    New price histories make past members eligible, so this works per (company, date), not
    per date. `rebuild` recomputes every pair. `workers` > 1 spreads companies over processes.
    """
    members = db.execute(
        select(UniverseMember.company_id, UniverseMember.security_id, UniverseMember.as_of).where(
            UniverseMember.version == UNIVERSE_VERSION,
            UniverseMember.as_of.in_(dates),
            UniverseMember.has_prices,
        )
    ).all()
    if rebuild:
        db.execute(
            delete(FeatureSnapshot).where(
                FeatureSnapshot.version == FEATURE_SET_VERSION, FeatureSnapshot.as_of.in_(dates)
            )
        )
        db.commit()
        existing: set[tuple[int, date]] = set()
    else:
        existing = set(
            db.execute(
                select(FeatureSnapshot.company_id, FeatureSnapshot.as_of).where(
                    FeatureSnapshot.version == FEATURE_SET_VERSION, FeatureSnapshot.as_of.in_(dates)
                )
            ).tuples()
        )
    by_company: dict[tuple[int, int], list[date]] = {}
    for m in members:
        if (m.company_id, m.as_of) not in existing:
            by_company.setdefault((m.company_id, m.security_id), []).append(m.as_of)
    jobs = [(c, s, d) for (c, s), d in by_company.items()]
    if not jobs:
        stored = skipped = 0
    elif workers <= 1:
        stored, skipped = _work(jobs)
    else:
        from concurrent.futures import ProcessPoolExecutor

        batches = [jobs[i::workers] for i in range(workers)]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_work, batches))
        stored, skipped = sum(r[0] for r in results), sum(r[1] for r in results)
    touched = sorted({d for days in by_company.values() for d in days})
    return {"stored": stored, "skipped": skipped, "companies": len(by_company), "dates": touched}
