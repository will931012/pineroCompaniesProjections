"""Price history for research: total-return series and a rate-limited bulk loader.

Tiingo's free plan allows 50 requests an hour and 1,000 a day (and 500 distinct symbols a
month). The loader counts this provider's recorded fetches over the last hour and day, so
every process (API, worker, CLI) shares one budget, and it stops when the budget is spent.
The job that runs it re-queues itself for the next hour until every target is current.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics.technicals import growth_index, total_returns
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import DailyPrice, PriceCoverage, ProviderFetch, Security
from app.market_data.service import _upsert_bars
from app.providers.base import ProviderError, record_fetch
from app.providers.market_data.base import MarketDataProvider

logger = logging.getLogger(__name__)

HISTORY_START = date(2010, 1, 1)
DAILY_CAP = 900
UPSERT_CHUNK = 2000  # 17 columns per row stays under PostgreSQL's 65,535 parameters

# Market reference series: broad indexes, sectors, rates, gold. Kept small for the budget.
MARKET_TICKERS = (
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "GLD", "TLT", "IEF",
)  # fmt: skip


@dataclass(frozen=True)
class ReturnSeries:
    """Dates and daily total returns (returns[i] is the return into dates[i + 1])."""

    dates: list[date]
    closes: list[float]
    returns: list[float]

    @property
    def index(self) -> list[float]:
        return growth_index(self.returns)

    def as_of(self, day: date) -> "ReturnSeries":
        """Only the bars on or before `day` (point in time)."""
        n = sum(1 for d in self.dates if d <= day)
        return ReturnSeries(self.dates[:n], self.closes[:n], self.returns[: max(n - 1, 0)])


def return_series(db: Session, security_id: int, provider: str = "tiingo") -> ReturnSeries:
    rows = db.execute(
        select(
            DailyPrice.trade_date, DailyPrice.close, DailyPrice.dividend_cash,
            DailyPrice.split_factor,
        )
        .where(DailyPrice.security_id == security_id, DailyPrice.provider == provider)
        .order_by(DailyPrice.trade_date)
    ).all()  # fmt: skip
    closes = [float(r.close) for r in rows]
    returns = total_returns(
        closes,
        [float(r.dividend_cash or 0) for r in rows],
        [float(r.split_factor or 1) for r in rows],
    )
    return ReturnSeries([r.trade_date for r in rows], closes, returns)


def requests_used(db: Session, provider: str, since: timedelta) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ProviderFetch)
            .where(
                ProviderFetch.provider == provider,
                ProviderFetch.started_at >= utcnow() - since,
            )
        )
        or 0
    )


def budget(db: Session, settings: Settings, provider: str = "tiingo") -> int:
    hourly = settings.price_loader_requests_per_hour - requests_used(
        db, provider, timedelta(hours=1)
    )
    daily = DAILY_CAP - requests_used(db, provider, timedelta(days=1))
    return max(0, min(hourly, daily))


def last_complete_day(today: date) -> date:
    """The latest weekday before today (bars for today may not be final)."""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def needs_load(coverage: PriceCoverage | None, target: date, retry_after: timedelta) -> bool:
    if coverage is None:
        return True
    if coverage.status in {"not_found", "error"}:
        return utcnow() - coverage.attempted_at > retry_after
    return coverage.loaded_through is None or coverage.loaded_through < target


@dataclass
class LoadReport:
    fetched: int = 0
    bars: int = 0
    not_found: int = 0
    errors: int = 0
    remaining: int = 0
    out_of_budget: bool = False


def load_securities(
    db: Session,
    provider: MarketDataProvider,
    settings: Settings,
    securities: list[Security],
    *,
    today: date | None = None,
) -> LoadReport:
    """Bring each security's bars up to the last complete day, oldest-missing first."""
    today = today or utcnow().date()
    target = last_complete_day(today)
    name = provider.info.name
    coverage = {
        c.security_id: c
        for c in db.scalars(
            select(PriceCoverage).where(
                PriceCoverage.provider == name,
                PriceCoverage.security_id.in_([s.id for s in securities]),
            )
        )
    }
    pending = [s for s in securities if needs_load(coverage.get(s.id), target, timedelta(days=7))]
    report = LoadReport(remaining=len(pending))
    allowance = budget(db, settings, name)
    for security in pending:
        if allowance <= 0:
            report.out_of_budget = True
            break
        current = coverage.get(security.id)
        start = (
            current.loaded_through - timedelta(days=7)
            if current and current.loaded_through
            else HISTORY_START
        )
        allowance -= 1
        report.fetched += 1
        try:
            result = provider.get_daily_bars(security.ticker, start, today)
        except ProviderError as error:
            if error.meta is not None:
                record_fetch(db, error.meta, status="error", error=error)
            status = "not_found" if error.code == "provider_symbol_not_found" else "error"
            _set_coverage(db, security.id, name, current, status=status, error=error.message)
            db.commit()
            report.not_found += status == "not_found"
            report.errors += status == "error"
            if error.code in {"provider_rate_limited", "provider_auth_failed"}:
                report.out_of_budget = error.code == "provider_rate_limited"
                break
            continue
        fetch = record_fetch(
            db, result.meta, status="success", record_count=len(result.data),
            rejected_count=result.rejected_count,
        )  # fmt: skip
        for i in range(0, len(result.data), UPSERT_CHUNK):
            _upsert_bars(db, security, name, fetch, result.data[i : i + UPSERT_CHUNK])
        bars = result.data
        _set_coverage(
            db, security.id, name, current, status="loaded",
            loaded_from=min((b.trade_date for b in bars), default=None),
            loaded_through=max((b.trade_date for b in bars), default=target),
        )  # fmt: skip
        db.commit()
        report.bars += len(bars)
        report.remaining -= 1
    return report


def _set_coverage(
    db: Session,
    security_id: int,
    provider: str,
    current: PriceCoverage | None,
    *,
    status: str,
    error: str | None = None,
    loaded_from: date | None = None,
    loaded_through: date | None = None,
) -> None:
    values = {
        "security_id": security_id,
        "provider": provider,
        "status": status,
        "error": error,
        "attempted_at": utcnow(),
        "loaded_from": min(
            [d for d in (loaded_from, current.loaded_from if current else None) if d],
            default=None,
        ),
        "loaded_through": loaded_through or (current.loaded_through if current else None),
    }
    statement = insert(PriceCoverage).values(values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["security_id", "provider"],
            set_={k: statement.excluded[k] for k in values if k not in {"security_id", "provider"}},
        )
    )
