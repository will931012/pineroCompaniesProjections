"""Macro series from FRED, stored with their vintages and read point in time.

`available_on` is the first date a value could be known:
- normally its ALFRED vintage start (`realtime_start`);
- but FRED's vintage history for many series begins years after the observation (for
  example when the series entered ALFRED). Then the first stored vintage is not when the value
  was first published, so the observation date plus the series' release lag is used instead.
  The value itself is from that first stored vintage and may include later revisions; this is
  recorded as a limitation, and the lag is conservative.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import MacroObservation, MacroSeries
from app.providers.base import ProviderError, record_fetch
from app.providers.fred import FredClient, Vintage

OBSERVATION_START = date(2005, 1, 1)
# A first vintage this long after the conservative release date means ALFRED history is missing.
VINTAGE_GAP = timedelta(days=30)


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    label: str
    source: str
    release_lag_days: int  # from the observation date FRED uses (period start for monthly)
    unit: str  # how to show it: "percent" | "index" | "thousands" | "level"
    # Daily market rates are not revised after publication, and FRED caps a request at 2,000
    # vintage dates; for them the current values are stored and dated by the release lag.
    vintages: bool = True


STL = "Federal Reserve Bank of St. Louis"
H15 = "Federal Reserve H.15"
# Government and Federal Reserve series only (public domain or free to reuse with credit).
CATALOG = (
    SeriesSpec("DGS10", "10-year Treasury yield", H15, 1, "percent", vintages=False),
    SeriesSpec("DGS2", "2-year Treasury yield", H15, 1, "percent", vintages=False),
    SeriesSpec("DTB3", "3-month Treasury bill", H15, 1, "percent", vintages=False),
    SeriesSpec("T10Y3M", "10-year minus 3-month spread", STL, 1, "percent", vintages=False),
    SeriesSpec("T10Y2Y", "10-year minus 2-year spread", STL, 1, "percent", vintages=False),
    SeriesSpec("FEDFUNDS", "Effective federal funds rate", "Federal Reserve H.15", 35, "percent"),
    SeriesSpec("CPIAUCSL", "Consumer price index", "U.S. Bureau of Labor Statistics", 45, "index"),
    SeriesSpec("UNRATE", "Unemployment rate", "U.S. Bureau of Labor Statistics", 40, "percent"),
    SeriesSpec("PAYEMS", "Nonfarm payrolls", "U.S. Bureau of Labor Statistics", 40, "thousands"),
    SeriesSpec("INDPRO", "Industrial production index", "Federal Reserve G.17", 50, "index"),
    SeriesSpec("ICSA", "Initial jobless claims", "U.S. Department of Labor", 6, "level"),
    SeriesSpec("NFCI", "Chicago Fed financial conditions", "Chicago Fed", 7, "index"),
)  # fmt: skip
BY_ID = {s.series_id: s for s in CATALOG}


def availability(vintages: list[Vintage], lag_days: int) -> list[tuple[Vintage, date]]:
    """Pair each vintage with the first date its value could have been known."""
    first_start: dict[date, date] = {}
    for v in vintages:
        current = first_start.get(v.observation_date)
        if current is None or v.realtime_start < current:
            first_start[v.observation_date] = v.realtime_start
    out = []
    for v in vintages:
        estimated = v.observation_date + timedelta(days=lag_days)
        is_first = v.realtime_start == first_start[v.observation_date]
        if is_first and v.realtime_start > estimated + VINTAGE_GAP:
            out.append((v, estimated))
        else:
            out.append((v, max(v.realtime_start, v.observation_date)))
    return out


def refresh_series(db: Session, client: FredClient, spec: SeriesSpec) -> int:
    """Replace a series' stored vintages with FRED's current full history."""
    try:
        info = client.series_info(spec.series_id)
        result = client.vintages(spec.series_id, OBSERVATION_START, all_vintages=spec.vintages)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        raise
    record_fetch(db, info.meta, status="success", record_count=1)
    fetch = record_fetch(
        db, result.meta, status="success", record_count=len(result.data),
        rejected_count=result.rejected_count,
    )  # fmt: skip
    series_row = {
        "series_id": spec.series_id,
        "title": info.data.title,
        "units": info.data.units,
        "frequency": info.data.frequency,
        "source": spec.source,
        "release_lag_days": spec.release_lag_days,
        "refreshed_at": utcnow(),
        "fetch_id": fetch.id,
    }
    statement = insert(MacroSeries).values(series_row)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["series_id"],
            set_={k: statement.excluded[k] for k in series_row if k != "series_id"},
        )
    )
    db.query(MacroObservation).filter(MacroObservation.series_id == spec.series_id).delete()
    rows = [
        {
            "series_id": spec.series_id,
            "observation_date": v.observation_date,
            "realtime_start": v.realtime_start,
            "realtime_end": v.realtime_end,
            "available_on": available,
            "value": v.value,
            "fetch_id": fetch.id,
        }
        for v, available in availability(result.data, spec.release_lag_days)
    ]
    for i in range(0, len(rows), 5000):
        db.execute(insert(MacroObservation).values(rows[i : i + 5000]).on_conflict_do_nothing())
    db.commit()
    return len(rows)


@dataclass(frozen=True)
class Point:
    observation_date: date
    value: float
    available_on: date


class MacroHistory:
    """All stored vintages of the catalog, answering "what was known on day D?" quickly."""

    def __init__(self, db: Session, series_ids: list[str] | None = None) -> None:
        ids = series_ids or [s.series_id for s in CATALOG]
        rows = db.execute(
            select(
                MacroObservation.series_id, MacroObservation.observation_date,
                MacroObservation.available_on, MacroObservation.value,
            )
            .where(MacroObservation.series_id.in_(ids))
            .order_by(MacroObservation.available_on)
        ).all()  # fmt: skip
        self._rows: dict[str, list[tuple[date, date, float]]] = defaultdict(list)
        for r in rows:
            self._rows[r.series_id].append((r.available_on, r.observation_date, float(r.value)))

    def has(self, series_id: str) -> bool:
        return bool(self._rows.get(series_id))

    def known(self, series_id: str, day: date) -> dict[date, Point]:
        """The latest known value of every observation published by `day`."""
        latest: dict[date, Point] = {}
        for available_on, observed, value in self._rows.get(series_id, []):
            if available_on > day:
                break
            latest[observed] = Point(observed, value, available_on)
        return latest

    def latest(self, series_id: str, day: date) -> Point | None:
        known = self.known(series_id, day)
        return known[max(known)] if known else None

    def change(self, series_id: str, day: date, periods_back: int) -> float | None:
        """Latest known value minus the value `periods_back` observations earlier (same vintage)."""
        known = self.known(series_id, day)
        if len(known) <= periods_back:
            return None
        ordered = sorted(known)
        return known[ordered[-1]].value - known[ordered[-1 - periods_back]].value

    def growth(self, series_id: str, day: date, periods_back: int) -> float | None:
        known = self.known(series_id, day)
        if len(known) <= periods_back:
            return None
        ordered = sorted(known)
        base = known[ordered[-1 - periods_back]].value
        return known[ordered[-1]].value / base - 1 if base else None


def macro_features(history: MacroHistory, day: date) -> tuple[dict[str, float | None], date | None]:
    """Macro inputs for models on `day`, and the latest publication date used."""
    used: list[date] = []

    def latest(series_id: str) -> float | None:
        point = history.latest(series_id, day)
        if point:
            used.append(point.available_on)
        return point.value if point else None

    features: dict[str, float | None] = {
        "macro_10y": latest("DGS10"),
        "macro_curve_10y3m": latest("T10Y3M"),
        "macro_unemployment": latest("UNRATE"),
        "macro_nfci": latest("NFCI"),
        "macro_cpi_yoy": history.growth("CPIAUCSL", day, 12),
        "macro_unemployment_change_3m": history.change("UNRATE", day, 3),
        "macro_10y_change_3m": _change_days(history, "DGS10", day, 91),
    }
    return features, max(used) if used else None


def _change_days(history: MacroHistory, series_id: str, day: date, days: int) -> float | None:
    now = history.latest(series_id, day)
    then = history.latest(series_id, day - timedelta(days=days))
    return now.value - then.value if now and then else None


def dashboard(history: MacroHistory, day: date) -> list[dict[str, Any]]:
    """Latest value, its date, and changes for each catalog series (for the Markets page)."""
    out = []
    for spec in CATALOG:
        known = history.known(spec.series_id, day)
        if not known:
            out.append({"series_id": spec.series_id, "label": spec.label, "available": False})
            continue
        ordered = sorted(known)
        last = known[ordered[-1]]
        cutoff = last.observation_date - timedelta(days=5 * 366)
        out.append(
            {
                "series_id": spec.series_id,
                "label": spec.label,
                "source": spec.source,
                "unit": spec.unit,
                "available": True,
                "value": last.value,
                "observation_date": last.observation_date,
                "available_on": last.available_on,
                "yoy_change": _year_ago_change(known, ordered, spec.unit),
                "history": [(d, known[d].value) for d in ordered if d >= cutoff][:: _thin(ordered)],
            }
        )
    return out


def _thin(ordered: list[date]) -> int:
    return max(1, len(ordered) // 300)


def _year_ago_change(known: dict[date, Point], ordered: list[date], unit: str) -> float | None:
    last = ordered[-1]
    earlier = [d for d in ordered if d <= last - timedelta(days=365)]
    if not earlier:
        return None
    base = known[earlier[-1]].value
    if unit in {"index", "thousands", "level"}:
        return known[last].value / base - 1 if base else None
    return known[last].value - base
