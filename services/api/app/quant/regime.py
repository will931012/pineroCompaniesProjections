"""Market regime: transparent rules over the S&P 500 (SPY) and two macro flags.

- Trend: SPY's close above or below its 200-day average.
- Volatility: SPY's 21-day realised volatility against its own trailing ten-year history; above
  the 80th percentile is "stressed".
- Flags (shown with the label, not part of it): an inverted 10-year minus 3-month curve, and
  tighter-than-average financial conditions (Chicago Fed NFCI above zero).

Each input uses only data up to the date, so labels can segment model results without
look-ahead. REGIME_VERSION changes whenever a rule does.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics import technicals as t
from app.db.models import MarketRegime
from app.quant.macro import MacroHistory
from app.quant.prices import ReturnSeries

REGIME_VERSION = "r1"
STRESS_PERCENTILE = 0.80
LABELS = ("Uptrend, calm", "Uptrend, stressed", "Downtrend, calm", "Downtrend, stressed")


@dataclass(frozen=True)
class Regime:
    as_of: date
    label: str
    components: dict[str, Any]


def classify(spy: ReturnSeries, macro: MacroHistory | None, day: date) -> Regime | None:
    series = spy.as_of(day)
    if len(series.closes) < 260:
        return None
    sma200 = t.sma(series.closes, 200)[-1]
    assert sma200 is not None
    uptrend = series.closes[-1] >= sma200
    vols = [v for v in t.rolling_volatility(series.returns[-2520:], 21) if v is not None]
    current = vols[-1]
    percentile = sum(1 for v in vols if v <= current) / len(vols)
    stressed = percentile > STRESS_PERCENTILE
    label = f"{'Uptrend' if uptrend else 'Downtrend'}, {'stressed' if stressed else 'calm'}"
    curve = macro.latest("T10Y3M", day) if macro else None
    nfci = macro.latest("NFCI", day) if macro else None
    return Regime(
        day,
        label,
        {
            "spy_close": series.closes[-1],
            "spy_sma200": sma200,
            "trend": "up" if uptrend else "down",
            "vol_21d": current,
            "vol_percentile_10y": percentile,
            "curve_10y3m": curve.value if curve else None,
            "curve_inverted": curve.value < 0 if curve else None,
            "nfci": nfci.value if nfci else None,
            "tight_conditions": nfci.value > 0 if nfci else None,
            "price_date": series.dates[-1],
        },
    )


def store_regimes(db: Session, spy: ReturnSeries, macro: MacroHistory, dates: list[date]) -> int:
    rows = []
    for day in dates:
        regime = classify(spy, macro, day)
        if regime:
            components = {
                k: (v.isoformat() if isinstance(v, date) else v)
                for k, v in regime.components.items()
            }
            rows.append(
                {"version": REGIME_VERSION, "as_of": day, "label": regime.label,
                 "components": components}
            )  # fmt: skip
    db.execute(
        delete(MarketRegime).where(
            MarketRegime.version == REGIME_VERSION, MarketRegime.as_of.in_(dates)
        )
    )
    if rows:
        db.execute(insert(MarketRegime).values(rows))
    db.commit()
    return len(rows)
