import logging
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics.price_metrics import daily_change
from app.companies.schemas import SourceRef
from app.companies.service import resolve_security
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import DailyPrice, ProviderFetch, Security
from app.market_data.schemas import DailyBarOut, DataQuality, MarketBarsResponse, PriceSummary
from app.providers.base import ProviderError, record_fetch
from app.providers.market_data.base import MarketDataProvider
from app.providers.market_data.registry import market_data_status

logger = logging.getLogger(__name__)

_PROVIDER_ERROR_STATUS = {
    "provider_symbol_not_found": 404,
    "provider_rate_limited": 429,
}


def _stored_bars(
    db: Session, security: Security, provider: str, start: date, end: date
) -> Sequence[DailyPrice]:
    return db.scalars(
        select(DailyPrice)
        .where(
            DailyPrice.security_id == security.id,
            DailyPrice.provider == provider,
            DailyPrice.trade_date.between(start, end),
        )
        .order_by(DailyPrice.trade_date)
    ).all()


def _single_fresh_covering_fetch(
    db: Session, bars: Sequence[DailyPrice], start: date, end: date, settings: Settings
) -> ProviderFetch | None:
    """A cache hit requires every bar to come from one fresh retrieval covering the range,
    so adjusted values share a single adjustment basis."""

    fetch_ids = {bar.fetch_id for bar in bars}
    if len(fetch_ids) != 1:
        return None
    fetch = db.get(ProviderFetch, fetch_ids.pop())
    if fetch is None:
        return None
    params = fetch.request_params
    ttl = timedelta(minutes=settings.market_data_cache_ttl_minutes)
    covers = (
        params.get("start", "9999") <= start.isoformat()
        and params.get("end", "") >= end.isoformat()
    )
    return fetch if covers and utcnow() - fetch.retrieved_at < ttl else None


def _upsert_bars(
    db: Session, security: Security, provider: str, fetch: ProviderFetch, bars: list
) -> None:
    if not bars:
        return
    rows = [
        {
            "security_id": security.id,
            "provider": provider,
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "adj_open": bar.adj_open,
            "adj_high": bar.adj_high,
            "adj_low": bar.adj_low,
            "adj_close": bar.adj_close,
            "adj_volume": bar.adj_volume,
            "dividend_cash": bar.dividend_cash,
            "split_factor": bar.split_factor,
            "fetch_id": fetch.id,
            "retrieved_at": fetch.retrieved_at,
        }
        for bar in bars
    ]
    statement = insert(DailyPrice).values(rows)
    updatable = {
        c: statement.excluded[c]
        for c in rows[0]
        if c not in {"security_id", "provider", "trade_date"}
    }
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["security_id", "provider", "trade_date"],
            set_=updatable,
        )
    )


def _summary(bars: Sequence[DailyPrice]) -> PriceSummary | None:
    if not bars:
        return None
    last = bars[-1]
    previous = bars[-2] if len(bars) > 1 else None
    use_adjusted = (
        previous is not None
        and last.adj_close is not None
        and previous.adj_close is not None
        and last.fetch_id == previous.fetch_id
    )
    if previous is None:
        return PriceSummary(
            as_of=last.trade_date,
            last_close=float(last.close),
            previous_close=None,
            change=None,
            change_percent=None,
            basis="raw",
        )
    if use_adjusted:
        assert last.adj_close is not None and previous.adj_close is not None
        result = daily_change(last.adj_close, previous.adj_close)
    else:
        result = daily_change(last.close, previous.close)
    return PriceSummary(
        as_of=last.trade_date,
        last_close=float(last.close),
        previous_close=float(previous.close),
        change=float(result.change),
        change_percent=float(result.change_percent) if result.change_percent is not None else None,
        basis="adjusted" if use_adjusted else "raw",
    )


def _to_out(bar: DailyPrice) -> DailyBarOut:
    def opt(value: object) -> float | None:
        return float(value) if value is not None else None  # type: ignore[arg-type]

    return DailyBarOut(
        date=bar.trade_date,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=bar.volume,
        adj_open=opt(bar.adj_open),
        adj_high=opt(bar.adj_high),
        adj_low=opt(bar.adj_low),
        adj_close=opt(bar.adj_close),
        dividend_cash=opt(bar.dividend_cash),
        split_factor=opt(bar.split_factor),
        fetch_id=bar.fetch_id,
    )


def _response(
    db: Session, security: Security, provider: str, bars: Sequence[DailyPrice], quality: DataQuality
) -> MarketBarsResponse:
    fetch_ids = {bar.fetch_id for bar in bars}
    fetches = db.scalars(select(ProviderFetch).where(ProviderFetch.id.in_(fetch_ids))).all()
    if len(fetch_ids) > 1:
        quality.warnings.append(
            "Bars come from several retrievals; provider-adjusted prices may use "
            "different adjustment bases."
        )
    return MarketBarsResponse(
        ticker=security.ticker,
        interval="1d",
        provider=provider,
        bars=[_to_out(bar) for bar in bars],
        summary=_summary(bars),
        quality=quality,
        sources=[
            SourceRef(
                fetch_id=f.id,
                provider=f.provider,
                dataset=f.dataset,
                source_url=f.source_url,
                retrieved_at=f.retrieved_at,
                license_note=f.license_note,
            )
            for f in sorted(fetches, key=lambda f: f.id)
        ],
    )


def get_daily_bars(
    db: Session,
    provider: MarketDataProvider | None,
    settings: Settings,
    ticker: str,
    start: date,
    end: date,
) -> MarketBarsResponse:
    if start > end:
        raise ApiError(422, "invalid_date_range", "The start date must not be after the end date.")
    if (end - start).days > 366 * 30:
        raise ApiError(422, "date_range_too_large", "Request at most 30 years of daily bars.")
    security = resolve_security(db, ticker)
    status = market_data_status(settings)

    if provider is None:
        stored = _stored_bars(db, security, status.name, start, end) if status.name else []
        if not stored:
            raise ApiError(503, status.code, status.message)
        assert status.name is not None
        return _response(
            db,
            security,
            status.name,
            stored,
            DataQuality(
                status="stale",
                rejected_count=0,
                warnings=[f"{status.message} Showing previously stored bars."],
            ),
        )

    name = provider.info.name
    stored = _stored_bars(db, security, name, start, end)
    if _single_fresh_covering_fetch(db, stored, start, end, settings) is not None:
        return _response(
            db, security, name, stored, DataQuality(status="cached", rejected_count=0, warnings=[])
        )

    try:
        result = provider.get_daily_bars(security.ticker, start, end)
    except ProviderError as error:
        if error.meta is not None:
            error.meta.subject = f"security:{security.id}"
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        if stored:
            return _response(
                db,
                security,
                name,
                stored,
                DataQuality(
                    status="stale",
                    rejected_count=0,
                    warnings=[f"Provider refresh failed ({error.code}); showing stored bars."],
                ),
            )
        raise ApiError(
            _PROVIDER_ERROR_STATUS.get(error.code, 503), error.code, error.message
        ) from error

    result.meta.subject = f"security:{security.id}"
    result.meta.request_params = {
        "ticker": security.ticker,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    fetch = record_fetch(
        db,
        result.meta,
        status="success",
        record_count=len(result.data),
        rejected_count=result.rejected_count,
    )
    _upsert_bars(db, security, name, fetch, result.data)
    db.commit()
    warnings = (
        [f"{result.rejected_count} provider rows failed validation and were excluded."]
        if result.rejected_count
        else []
    )
    return _response(
        db,
        security,
        name,
        _stored_bars(db, security, name, start, end),
        DataQuality(status="fresh", rejected_count=result.rejected_count, warnings=warnings),
    )
