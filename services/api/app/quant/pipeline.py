"""Phase 6 orchestration shared by worker jobs and the CLI."""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import (
    Company,
    FinancialFact,
    MarketRate,
    Security,
    UniverseCandidate,
    UniverseMember,
)
from app.fundamentals.service import sync_company
from app.providers.base import ProviderError, record_fetch
from app.providers.fred import FredClient
from app.providers.market_data.base import MarketDataProvider
from app.providers.sec_edgar import SecEdgarClient
from app.providers.treasury import TreasuryClient
from app.quant import features, macro, modeling, regime, universe
from app.quant.factors import compute_factor_scores
from app.quant.journal import predict_latest, score_outcomes
from app.quant.prices import MARKET_TICKERS, load_securities, return_series
from app.valuation.service import RATE_SERIES

logger = logging.getLogger(__name__)

HISTORY_FROM = date(2012, 1, 1)


def refresh_macro(db: Session, fred: FredClient) -> dict[str, Any]:
    if not fred.configured:
        return {"skipped": "FRED_API_KEY not set"}
    out: dict[str, Any] = {}
    for spec in macro.CATALOG:
        try:
            out[spec.series_id] = macro.refresh_series(db, fred, spec)
        except ProviderError as error:
            out[spec.series_id] = f"error: {error.code}"
    return out


def refresh_treasury_history(
    db: Session, client: TreasuryClient, first_year: int = 2011, today: date | None = None
) -> dict[str, Any]:
    """Load each year's par yield curve once; the current year is reloaded every run."""
    today = today or utcnow().date()
    year_column = func.extract("year", MarketRate.observed_on)
    counts = {
        int(year): int(count)
        for year, count in db.execute(
            select(year_column, func.count())
            .where(MarketRate.series == RATE_SERIES)
            .group_by(year_column)
        ).all()
    }
    loaded = []
    for year in range(first_year, today.year + 1):
        if year < today.year and counts.get(year, 0) >= 2000:
            continue
        try:
            result = client.yield_curve(year)
        except ProviderError as error:
            if error.meta is not None:
                record_fetch(db, error.meta, status="error", error=error)
                db.commit()
            continue
        fetch = record_fetch(
            db, result.meta, status="success", record_count=len(result.data),
            rejected_count=result.rejected_count,
        )  # fmt: skip
        rows = [
            {"series": RATE_SERIES, "tenor": o.tenor, "observed_on": o.observed_on,
             "value": Decimal(str(round(o.value, 8))), "fetch_id": fetch.id}
            for o in result.data
        ]  # fmt: skip
        for i in range(0, len(rows), 5000):
            db.execute(insert(MarketRate).values(rows[i : i + 5000]).on_conflict_do_nothing())
        db.commit()
        loaded.append(year)
    return {"years_loaded": loaded}


def build_universe(
    db: Session, sec: SecEdgarClient, settings: Settings, *, today: date | None = None,
    sync_limit: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Candidates from SEC frames, their filings, then month-end membership."""
    today = today or utcnow().date()
    report: dict[str, Any] = {}
    candidates = universe.build_candidates(db, sec, last_year=today.year - 1)
    report["candidates"] = candidates.__dict__
    ids = list(db.scalars(select(UniverseCandidate.company_id)))
    with_facts = set(
        db.scalars(
            select(FinancialFact.company_id).where(FinancialFact.company_id.in_(ids)).distinct()
        )
    )
    to_sync = [i for i in ids if i not in with_facts][:sync_limit]
    synced = failed = 0
    for company_id in to_sync:
        company = db.get(Company, company_id)
        if company is None:
            continue
        status, _ = sync_company(db, company, sec, settings)
        synced += status == "current"
        failed += status != "current"
    report["synced"], report["sync_failed"] = synced, failed
    report["ranked"] = universe.rank_universe(
        db, settings, universe.month_ends(HISTORY_FROM, today)
    )
    return report


# Funds SEC's ticker file omits (series of a parent trust), keyed by ticker: (name, exchange).
REFERENCE_FUNDS = {
    "QQQ": ("Invesco QQQ Trust", "Nasdaq"),
    "IWM": ("iShares Russell 2000 ETF", "NYSE Arca"),
    "XLK": ("Technology Select Sector SPDR Fund", "NYSE Arca"),
    "XLF": ("Financial Select Sector SPDR Fund", "NYSE Arca"),
    "XLE": ("Energy Select Sector SPDR Fund", "NYSE Arca"),
    "XLV": ("Health Care Select Sector SPDR Fund", "NYSE Arca"),
    "XLY": ("Consumer Discretionary Select Sector SPDR Fund", "NYSE Arca"),
    "XLP": ("Consumer Staples Select Sector SPDR Fund", "NYSE Arca"),
    "XLI": ("Industrial Select Sector SPDR Fund", "NYSE Arca"),
    "XLB": ("Materials Select Sector SPDR Fund", "NYSE Arca"),
    "XLU": ("Utilities Select Sector SPDR Fund", "NYSE Arca"),
    "XLRE": ("Real Estate Select Sector SPDR Fund", "NYSE Arca"),
    "XLC": ("Communication Services Select Sector SPDR Fund", "NYSE Arca"),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", "Nasdaq"),
    "IEF": ("iShares 7-10 Year Treasury Bond ETF", "Nasdaq"),
}


def ensure_reference_listings(db: Session) -> list[str]:
    """Create listings for market reference funds missing from SEC's ticker file.

    They have no SEC CIK and no source fetch, so the directory sync never deactivates them.
    """
    present = set(
        db.scalars(
            select(Security.ticker).where(Security.ticker.in_(MARKET_TICKERS), Security.is_active)
        )
    )
    created = []
    for ticker in MARKET_TICKERS:
        if ticker in present or ticker not in REFERENCE_FUNDS:
            continue
        name, exchange = REFERENCE_FUNDS[ticker]
        company = Company(legal_name=name, entity_type="fund (reference list)")
        db.add(company)
        db.flush()
        db.add(Security(company_id=company.id, ticker=ticker, exchange=exchange, currency="USD",
                        is_active=True, first_seen_at=utcnow(), last_seen_at=utcnow()))  # fmt: skip
        created.append(ticker)
    db.commit()
    return created


def price_targets(db: Session) -> list[Security]:
    market = list(
        db.scalars(select(Security).where(Security.ticker.in_(MARKET_TICKERS), Security.is_active))
    )
    seen = {s.id for s in market}
    return market + [s for s in universe.price_targets(db) if s.id not in seen]


def load_prices_step(db: Session, market: MarketDataProvider, settings: Settings) -> dict[str, Any]:
    ensure_reference_listings(db)
    report = load_securities(db, market, settings, price_targets(db))
    if report.fetched:
        universe.refresh_has_prices(db)
    return report.__dict__


def update_research(db: Session, settings: Settings, workers: int = 1) -> dict[str, Any]:
    """New month-end features, regimes, factor scores; then predictions and outcomes."""
    out: dict[str, Any] = {}
    ranked = set(
        db.scalars(
            select(UniverseMember.as_of)
            .where(UniverseMember.version == universe.UNIVERSE_VERSION)
            .distinct()
        )
    )
    if ranked:
        missing = [d for d in universe.month_ends(HISTORY_FROM, utcnow().date()) if d not in ranked]
        if missing:
            out["ranked"] = universe.rank_universe(db, settings, missing)
    member_dates = sorted(
        db.scalars(
            select(UniverseMember.as_of)
            .where(UniverseMember.version == universe.UNIVERSE_VERSION, UniverseMember.has_prices)
            .distinct()
        )
    )
    built = features.build_snapshots(db, member_dates, workers)
    touched = built.pop("dates")
    out["features"] = built
    if touched:
        out["factor_scores"] = compute_factor_scores(db, touched)  # type: ignore[arg-type]
    market = features.load_market_inputs(db)
    all_month_ends = universe.month_ends(HISTORY_FROM, utcnow().date())
    out["regimes"] = regime.store_regimes(db, market.benchmark, market.macro, all_month_ends)
    out["predictions"] = predict_latest(db, market.benchmark)
    out["outcomes_scored"] = score_outcomes(db, market.benchmark)
    return out


def train_models(db: Session) -> dict[str, Any]:
    benchmark = features.load_market_inputs(db).benchmark
    out: dict[str, Any] = {}
    for horizon in modeling.HORIZONS:
        version = modeling.train_and_store(db, benchmark, horizon)
        out[f"{horizon}d"] = str(version.id) if version else "skipped (too few labelled rows)"
    out["predictions"] = predict_latest(db, benchmark)
    return out


def benchmark_series(db: Session) -> Any:
    spy = db.scalar(select(Security).where(Security.ticker == "SPY", Security.is_active))
    return return_series(db, spy.id) if spy else None
