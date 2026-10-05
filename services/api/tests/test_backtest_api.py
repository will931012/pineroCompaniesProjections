"""Backtest API against a synthetic point-in-time universe."""

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import DailyPrice, FactorScore, ProviderFetch, Security, UniverseMember
from app.quant.universe import month_ends
from tests.conftest import LoggedIn

pytestmark = pytest.mark.integration

TICKERS = [f"S{i:02d}" for i in range(1, 26)]
ROWS: list[list[object]] = [
    [700000 + i, f"Synthetic {t}", t, "NYSE"] for i, t in enumerate(TICKERS)
]
ROWS.append([884394, "SPDR S&P 500 ETF Trust", "SPY", "NYSE"])
START = date(2023, 1, 2)


def _trading_days(n: int) -> list[date]:
    out, day = [], START
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


@pytest.fixture
def market(seed_directory, db: Session) -> list[date]:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    fetch = ProviderFetch(
        provider="tiingo",
        dataset="test",
        source_url="https://example.test",
        request_params={},
        status="success",
        license_note="test",
        started_at=utcnow(),
        retrieved_at=utcnow(),
        latency_ms=0,
    )
    db.add(fetch)
    db.flush()
    days = _trading_days(380)
    securities = {s.ticker: s for s in db.scalars(select(Security))}
    for rank, ticker in enumerate(["SPY", *TICKERS]):
        drift = 0.0003 if ticker == "SPY" else 0.0001 * rank  # higher score, higher drift
        price = 50.0
        rows = []
        for n, day in enumerate(days):
            price *= 1 + drift + 0.003 * math.sin(n + rank)
            value = Decimal(str(round(price, 4)))
            rows.append(
                DailyPrice(
                    security_id=securities[ticker].id,
                    provider="tiingo",
                    trade_date=day,
                    open=value,
                    high=Decimal(str(round(price * 1.005, 4))),
                    low=Decimal(str(round(price * 0.995, 4))),
                    close=value,
                    volume=2_000_000,
                    fetch_id=fetch.id,
                    retrieved_at=utcnow(),
                )
            )
        db.add_all(rows)
    signals = month_ends(START, days[-1])
    for as_of in signals:
        for i, ticker in enumerate(TICKERS):
            security = securities[ticker]
            db.add(
                UniverseMember(
                    version="u1",
                    as_of=as_of,
                    company_id=security.company_id,
                    rank=i + 1,
                    revenue_ttm=Decimal(1000 - i),
                    revenue_basis="FY",
                    security_id=security.id,
                    has_prices=True,
                )
            )
            db.add(
                FactorScore(
                    version="fs1",
                    as_of=as_of,
                    company_id=security.company_id,
                    factor="quality",
                    score=i / 25,
                    percentile=(i + 1) / 25,
                    inputs={},
                )
            )
    db.commit()
    return signals


def _post(user: LoggedIn, body: dict[str, Any]):  # type: ignore[no-untyped-def]
    return user.client.post("/api/v1/backtests", json=body, headers={"X-CSRF-Token": user.csrf})


SPEC = {
    "name": "Quality top 5",
    "factors": [{"factor": "quality", "weight": 1}],
    "top_n": 5,
    "max_weight": 0.25,
}


def test_options_list_factors_and_signal_dates(analyst: LoggedIn, market: list[date]) -> None:
    body = analyst.client.get("/api/v1/backtests/options").json()
    assert [f["key"] for f in body["factors"]] == [
        "value",
        "quality",
        "momentum",
        "low_volatility",
        "growth",
        "size",
    ]
    assert body["first_signal"] == market[0].isoformat()
    assert body["last_signal"] == market[-1].isoformat()
    assert {c["key"] for c in body["costs"]} >= {"commission_per_share", "max_participation"}


def test_backtest_holds_the_top_scores_and_trades_next_day(
    analyst: LoggedIn, market: list[date]
) -> None:
    response = _post(analyst, SPEC)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "done", body["error"]
    held = sorted(p["ticker"] for p in body["results"]["holdings"]["positions"])
    assert held == ["S21", "S22", "S23", "S24", "S25"]
    assert all(
        p["weight"] == pytest.approx(0.2, abs=0.01)
        for p in body["results"]["holdings"]["positions"]
    )
    stats = body["results"]["stats"]
    assert stats["before_costs"]["cagr"] > stats["strategy"]["cagr"]  # costs are charged
    assert body["results"]["trading"]["costs_total"] > 0
    assert body["results"]["trading"]["rebalances"] == len(market)
    assert body["results"]["coverage"][0] == {
        "as_of": market[0].isoformat(),
        "universe": 25,
        "eligible": 25,
        "selected": 5,
    }
    assert body["summary"]["trials"] == 1 and body["summary"]["dsr"] is None
    assert body["results"]["series"]["spy"][0][1] == pytest.approx(1_000_000)


def test_runs_are_private_and_counted_as_trials(
    analyst: LoggedIn,
    market: list[date],
    make_user,
    login,  # type: ignore[no-untyped-def]
) -> None:
    first = _post(analyst, SPEC).json()
    second = _post(analyst, {**SPEC, "name": "Quality top 10", "top_n": 10}).json()
    other = login(make_user("other@example.com"))

    assert second["summary"]["trials"] == 2
    assert [b["name"] for b in analyst.client.get("/api/v1/backtests").json()] == [
        "Quality top 10",
        "Quality top 5",
    ]
    assert other.client.get(f"/api/v1/backtests/{first['id']}").status_code == 404
    assert other.client.get("/api/v1/backtests").json() == []
    deleted = analyst.client.delete(
        f"/api/v1/backtests/{first['id']}", headers={"X-CSRF-Token": analyst.csrf}
    )
    assert deleted.status_code == 204
    assert analyst.client.get(f"/api/v1/backtests/{first['id']}").status_code == 404


def test_invalid_and_too_short_backtests(analyst: LoggedIn, market: list[date]) -> None:
    duplicate = _post(analyst, {**SPEC, "factors": [{"factor": "quality", "weight": 1}] * 2})
    reversed_dates = _post(analyst, {**SPEC, "start": "2024-05-01", "end": "2024-01-01"})
    short = _post(analyst, {**SPEC, "start": "2024-01-01"}).json()

    assert duplicate.status_code == 422 and reversed_dates.status_code == 422
    assert short["status"] == "failed" and "Fewer than 13 signal dates" in short["error"]
