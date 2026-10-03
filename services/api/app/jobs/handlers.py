"""Background job handlers. Each takes (db, context, payload) and returns a JSON summary."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.alerts.service import deliver_pending, evaluate_all, tracked_company_ids
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import Company
from app.events.service import build_filing_events, refresh_company_news, tickers_of
from app.filings.service import INSIDER_FORMS, embed_missing, load_pending, refresh_filing_index
from app.providers.email import EmailSender
from app.providers.embeddings import EmbeddingProvider
from app.providers.fred import FredClient
from app.providers.gdelt import GdeltClient
from app.providers.market_data.base import MarketDataProvider
from app.providers.openfigi import OpenFigiClient
from app.providers.sec_edgar import SecEdgarClient
from app.providers.treasury import TreasuryClient

logger = logging.getLogger(__name__)


@dataclass
class WorkerContext:
    settings: Settings
    sec: SecEdgarClient
    gdelt: GdeltClient
    embedder: EmbeddingProvider | None
    email: EmailSender | None
    market: MarketDataProvider | None
    fred: FredClient | None = None
    figi: OpenFigiClient | None = None
    treasury: TreasuryClient | None = None


Handler = Callable[[Session, WorkerContext, dict[str, Any]], dict[str, Any]]


def _companies(db: Session, payload: dict[str, Any]) -> list[Company]:
    ids = payload.get("company_ids") or tracked_company_ids(db)
    return [c for c in (db.get(Company, i) for i in ids) if c is not None]


def refresh_filings(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    """New 8-Ks and Form 4s for tracked companies, their events, and search embeddings."""
    summary: dict[str, Any] = {"companies": 0, "events": 0}
    for company in _companies(db, payload):
        status, _ = refresh_filing_index(db, company, ctx.sec, ctx.settings)
        if status not in {"current", "stale"}:
            continue
        load_pending(db, company, ctx.sec, ("8-K", "8-K/A"), 5)
        load_pending(db, company, ctx.sec, INSIDER_FORMS, 20)
        summary["events"] += build_filing_events(db, company)
        if ctx.embedder is not None:
            embed_missing(db, company.id, ctx.embedder, 200)
        summary["companies"] += 1
    return summary


def poll_news(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"companies": 0, "new": 0, "events": 0, "errors": 0}
    for company in _companies(db, payload):
        outcome = refresh_company_news(db, company, ctx.gdelt, ctx.embedder, timespan="3d")
        summary["companies"] += 1
        summary["new"] += outcome.get("new", 0)
        summary["events"] += outcome.get("events", 0)
        summary["errors"] += outcome.get("status") == "error"
    return summary


def refresh_prices(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.market_data.service import get_daily_bars

    if ctx.market is None:
        return {"skipped": "no market-data provider configured"}
    today = utcnow().date()
    refreshed = 0
    for company in _companies(db, payload):
        tickers = tickers_of(db, company)
        if not tickers:
            continue
        try:
            get_daily_bars(
                db, ctx.market, ctx.settings, tickers[0], today - timedelta(days=10), today
            )
            refreshed += 1
        except ApiError as error:
            logger.warning("price_refresh_failed", extra={"ticker": tickers[0], "code": error.code})
    return {"refreshed": refreshed}


def evaluate_alerts(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    created = evaluate_all(db, email_ready=ctx.email is not None)
    delivered = deliver_pending(db, ctx.email, ctx.settings)
    return {"created": created, **delivered}


def refresh_macro(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    """FRED series (when a key is set) and the Treasury par yield history."""
    from app.quant import pipeline

    out: dict[str, Any] = {}
    if ctx.fred is not None:
        out["fred"] = pipeline.refresh_macro(db, ctx.fred)
    if ctx.treasury is not None:
        out["treasury"] = pipeline.refresh_treasury_history(db, ctx.treasury)
    return out


def load_prices(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Universe and market prices within Tiingo's hourly and daily budget."""
    from app.jobs.queue import enqueue
    from app.quant import pipeline

    if ctx.market is None:
        return {"skipped": "no market-data provider configured"}
    report = pipeline.load_prices_step(db, ctx.market, ctx.settings)
    if report.get("remaining") and report.get("out_of_budget"):
        # Continue at the top of the next hour, when the hourly budget has room again.
        next_hour = utcnow().replace(minute=1, second=0, microsecond=0) + timedelta(hours=1)
        enqueue(db, "load_prices", run_at=next_hour, dedupe_key=f"load_prices:{next_hour:%Y%m%d%H}")
    return report


def load_bitcoin(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.providers.market_data.tiingo import TiingoMarketDataProvider
    from app.quant.bitcoin import load_bitcoin as load

    if not isinstance(ctx.market, TiingoMarketDataProvider):
        return {"skipped": "Bitcoin prices need the Tiingo provider"}
    return load(db, ctx.market, ctx.settings)


def build_universe(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.quant import pipeline

    return pipeline.build_universe(db, ctx.sec, ctx.settings, sync_limit=payload.get("sync_limit"))


def update_research(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.quant import pipeline

    return pipeline.update_research(db, ctx.settings, int(payload.get("workers", 1)))


def train_models(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.quant import pipeline

    return pipeline.train_models(db)


def refresh_ownership(db: Session, ctx: WorkerContext, payload: dict[str, Any]) -> dict[str, Any]:
    from app.quant.ownership import refresh_ownership as refresh

    if ctx.figi is None:
        return {"skipped": "OpenFIGI client unavailable"}
    return {"datasets": refresh(db, ctx.sec, ctx.figi, datasets=int(payload.get("datasets", 2)))}


HANDLERS: dict[str, Handler] = {
    "refresh_filings": refresh_filings,
    "poll_news": poll_news,
    "refresh_prices": refresh_prices,
    "evaluate_alerts": evaluate_alerts,
    "refresh_macro": refresh_macro,
    "load_prices": load_prices,
    "load_bitcoin": load_bitcoin,
    "build_universe": build_universe,
    "update_research": update_research,
    "train_models": train_models,
    "refresh_ownership": refresh_ownership,
}
