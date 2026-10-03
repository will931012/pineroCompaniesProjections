"""Bitcoin: daily prices from Tiingo's crypto endpoint and their analysis.

Bitcoin trades every day, so its statistics use 365 periods a year. Comparisons with stocks,
gold and yields use only the days both markets were open, with returns measured between
consecutive shared days. The halving dates are protocol events (blocks 210,000 × n) and are
fixed facts, not estimates.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics import technicals as t
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import CryptoPrice, MarketRate
from app.providers.base import ProviderError, record_fetch
from app.providers.market_data.tiingo import TiingoMarketDataProvider
from app.quant.prices import budget

PAIR = "btcusd"
HISTORY_START = date(2011, 8, 1)
DAYS_PER_YEAR = 365
# Block heights 210,000; 420,000; 630,000; 840,000.
HALVINGS = (date(2012, 11, 28), date(2016, 7, 9), date(2020, 5, 11), date(2024, 4, 20))
MAX_PAGES = 6


def load_bitcoin(
    db: Session, provider: TiingoMarketDataProvider, settings: Settings, today: date | None = None
) -> dict[str, Any]:
    """Fetch new daily bars, paging by date because one response holds about 4,400 rows."""
    today = today or utcnow().date()
    last = db.scalar(select(func.max(CryptoPrice.trade_date)).where(CryptoPrice.pair == PAIR))
    start = last - timedelta(days=3) if last else HISTORY_START
    stored = pages = 0
    while start <= today and pages < MAX_PAGES:
        if budget(db, settings, provider.info.name) <= 0:
            return {"stored": stored, "pages": pages, "out_of_budget": True}
        pages += 1
        try:
            result = provider.get_crypto_bars(PAIR, start, today)
        except ProviderError as error:
            if error.meta is not None:
                record_fetch(db, error.meta, status="error", error=error)
                db.commit()
            raise
        fetch = record_fetch(
            db, result.meta, status="success", record_count=len(result.data),
            rejected_count=result.rejected_count,
        )  # fmt: skip
        if not result.data:
            db.commit()
            break
        rows = [
            {
                "pair": PAIR, "trade_date": b.trade_date, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume, "volume_usd": b.volume_usd,
                "trades": b.trades, "fetch_id": fetch.id,
            }
            for b in result.data
        ]  # fmt: skip
        for i in range(0, len(rows), 3000):
            statement = insert(CryptoPrice).values(rows[i : i + 3000])
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=["pair", "trade_date"],
                    set_={
                        k: statement.excluded[k] for k in rows[0] if k not in {"pair", "trade_date"}
                    },
                )
            )
        db.commit()
        stored += len(rows)
        newest = max(b.trade_date for b in result.data)
        if newest >= today - timedelta(days=1):
            break
        start = newest + timedelta(days=1)
    return {"stored": stored, "pages": pages, "out_of_budget": False}


@dataclass(frozen=True)
class Daily:
    dates: list[date]
    closes: list[float]


def bitcoin_series(db: Session) -> Daily:
    rows = db.execute(
        select(CryptoPrice.trade_date, CryptoPrice.close)
        .where(CryptoPrice.pair == PAIR)
        .order_by(CryptoPrice.trade_date)
    ).all()
    return Daily([r.trade_date for r in rows], [float(r.close) for r in rows])


def ten_year_yield(db: Session) -> Daily:
    rows = db.execute(
        select(MarketRate.observed_on, MarketRate.value)
        .where(MarketRate.series == "treasury_par", MarketRate.tenor == "10 Yr")
        .order_by(MarketRate.observed_on)
    ).all()
    return Daily([r.observed_on for r in rows], [float(r.value) for r in rows])


def _days_back(dates: Sequence[date], closes: Sequence[float], days: int) -> float | None:
    """Return over the last `days` calendar days (to the latest close on or before the start)."""
    if not dates:
        return None
    target = dates[-1] - timedelta(days=days)
    earlier = [c for d, c in zip(dates, closes, strict=True) if d <= target]
    return closes[-1] / earlier[-1] - 1 if earlier and earlier[-1] > 0 else None


def aligned_returns(
    a: Daily, b: Daily, *, b_is_level: bool = False
) -> tuple[list[date], list[float], list[float]]:
    """Returns of `a` and `b` between consecutive dates both have. A level series (yields)
    contributes its change rather than a percentage return."""
    b_by_date = dict(zip(b.dates, b.closes, strict=True))
    shared = [
        (d, c, b_by_date[d]) for d, c in zip(a.dates, a.closes, strict=True) if d in b_by_date
    ]
    dates, ra, rb = [], [], []
    for (_, a0, b0), (d1, a1, b1) in pairwise(shared):
        if a0 <= 0 or (not b_is_level and b0 <= 0):
            continue
        dates.append(d1)
        ra.append(a1 / a0 - 1)
        rb.append(b1 - b0 if b_is_level else b1 / b0 - 1)
    return dates, ra, rb


@dataclass
class Relationship:
    name: str
    measure: str  # "return" or "change in yield"
    window_days: int
    correlation: float | None
    beta: float | None
    observations: int
    rolling: list[tuple[date, float | None]] = field(default_factory=list)


def relationship(
    name: str, btc: Daily, other: Daily, *, level: bool = False, window: int = 90
) -> Relationship:
    """Correlation and beta over the last `window` shared days, plus a rolling series."""
    dates, rb, ro = aligned_returns(btc, other, b_is_level=level)
    recent_b, recent_o = rb[-window:], ro[-window:]
    rolling = t.rolling(rb, ro, window, "correlation")
    step = max(1, len(dates) // 400)  # thin the chart series
    return Relationship(
        name,
        "change in yield" if level else "return",
        window,
        t.correlation(recent_b, recent_o),
        t.beta(recent_b, recent_o),
        len(recent_b),
        [(d, v) for d, v in list(zip(dates, rolling, strict=True))[::step] if v is not None],
    )


@dataclass
class HalvingCycle:
    halving: date
    price_at_halving: float
    days_observed: int
    # Price ÷ price at the halving, by days since the halving (weekly points).
    path: list[tuple[int, float]]
    return_1y: float | None
    peak_multiple: float | None
    days_to_peak: int | None
    drawdown_after_peak: float | None


def halving_cycles(series: Daily, today: date | None = None) -> list[HalvingCycle]:
    today = today or (series.dates[-1] if series.dates else utcnow().date())
    cycles = []
    for i, halving in enumerate(HALVINGS):
        end = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else today + timedelta(days=1)
        points = [
            (d, c) for d, c in zip(series.dates, series.closes, strict=True) if halving <= d < end
        ]
        if not points or points[0][0] > halving + timedelta(days=7):
            continue
        base = points[0][1]
        multiples = [((d - halving).days, c / base) for d, c in points]
        peak_day, peak = max(multiples, key=lambda m: m[1])
        after = [m for day, m in multiples if day >= peak_day]
        one_year = [m for day, m in multiples if day <= 365]
        cycles.append(
            HalvingCycle(
                halving=halving,
                price_at_halving=base,
                days_observed=multiples[-1][0],
                path=multiples[::7],
                return_1y=one_year[-1] - 1 if multiples[-1][0] >= 365 else None,
                peak_multiple=peak,
                days_to_peak=peak_day,
                drawdown_after_peak=min(after) / peak - 1 if after else None,
            )
        )
    return cycles


def summary(series: Daily) -> dict[str, Any]:
    """Headline statistics for the latest day."""
    closes, dates = series.closes, series.dates
    if len(closes) < 2:
        return {}
    returns = [b / a - 1 for a, b in pairwise(closes) if a > 0]
    drawdown = t.drawdowns(closes)
    peak_index = max(range(len(closes)), key=lambda i: closes[i])
    sma50, sma200 = t.sma(closes, 50)[-1], t.sma(closes, 200)[-1]
    rsi = t.rsi(closes, 14)[-1]
    year_start = [c for d, c in zip(dates, closes, strict=True) if d.year < dates[-1].year]
    return {
        "as_of": dates[-1],
        "close": closes[-1],
        "returns": {
            "1d": closes[-1] / closes[-2] - 1,
            "7d": _days_back(dates, closes, 7),
            "30d": _days_back(dates, closes, 30),
            "90d": _days_back(dates, closes, 90),
            "ytd": closes[-1] / year_start[-1] - 1 if year_start else None,
            "1y": _days_back(dates, closes, 365),
            "3y": _days_back(dates, closes, 3 * 365),
        },
        "volatility": {
            "30d": t.annualised_volatility(returns[-30:], DAYS_PER_YEAR),
            "90d": t.annualised_volatility(returns[-90:], DAYS_PER_YEAR),
            "1y": t.annualised_volatility(returns[-365:], DAYS_PER_YEAR),
        },
        "all_time_high": closes[peak_index],
        "all_time_high_date": dates[peak_index],
        "drawdown": drawdown[-1],
        "max_drawdown_1y": min(drawdown[-365:]) if len(drawdown) >= 2 else None,
        "sma_50": sma50,
        "sma_200": sma200,
        "above_sma_200": closes[-1] > sma200 if sma200 else None,
        "rsi_14": rsi,
        "log_growth_per_year": (
            math.log(closes[-1] / closes[0]) / ((dates[-1] - dates[0]).days / DAYS_PER_YEAR)
            if closes[0] > 0 and dates[-1] > dates[0]
            else None
        ),
    }
