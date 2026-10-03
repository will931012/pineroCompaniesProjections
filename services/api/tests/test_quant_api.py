"""Phase 6 API and pipeline behaviour against PostgreSQL."""

import math
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.routes import get_sec_client
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import (
    Company,
    CryptoPrice,
    DailyPrice,
    InstitutionalHolding,
    Job,
    MacroObservation,
    MacroSeries,
    ModelVersion,
    OwnershipSummary,
    Prediction,
    PredictionOutcome,
    PriceCoverage,
    ProviderFetch,
    Security,
    UniverseCandidate,
    UniverseMember,
)
from app.providers.base import FetchMeta, FetchResult
from app.providers.market_data.base import DailyBar
from app.providers.market_data.tiingo import INFO
from app.quant import pipeline, universe
from app.quant.prices import load_securities
from tests.conftest import LoggedIn
from tests.fakes import sec_client_with
from tests.fixtures_companyfacts import CIK, companyfacts

pytestmark = pytest.mark.integration

ROWS = [
    [CIK, "Example Devices Inc.", "EXDV", "Nasdaq"],
    [999002, "Example Peer Corp", "EXPR", "NYSE"],
    [884394, "SPDR S&P 500 ETF Trust", "SPY", "NYSE"],
    [1222333, "SPDR Gold Trust", "GLD", "NYSE"],
]


def _fetch(db: Session, provider: str = "tiingo") -> ProviderFetch:
    meta = FetchMeta(provider, "test", "https://example.test", "test fixture")
    meta.finish(200, b"")
    fetch = ProviderFetch(
        provider=provider,
        dataset="test",
        source_url=meta.source_url,
        request_params={},
        status="success",
        license_note="test",
        started_at=meta.started_at,
        retrieved_at=meta.retrieved_at,
        latency_ms=0,
    )
    db.add(fetch)
    db.flush()
    return fetch


def _bars(
    db: Session, ticker: str, days: int, drift: float, start: date = date(2023, 1, 2)
) -> None:
    security = db.scalar(select(Security).where(Security.ticker == ticker))
    assert security is not None
    fetch = _fetch(db)
    day, price, n = start, 100.0, 0
    while n < days:
        if day.weekday() < 5:
            price *= 1 + drift + 0.004 * math.sin(n)
            db.add(
                DailyPrice(
                    security_id=security.id,
                    provider="tiingo",
                    trade_date=day,
                    open=Decimal(str(round(price, 4))),
                    high=Decimal(str(round(price * 1.01, 4))),
                    low=Decimal(str(round(price * 0.99, 4))),
                    close=Decimal(str(round(price, 4))),
                    volume=1000,
                    fetch_id=fetch.id,
                    retrieved_at=utcnow(),
                )
            )
            n += 1
        day += timedelta(days=1)
    db.commit()


@pytest.fixture
def seeded(analyst: LoggedIn, override, seed_directory, db: Session) -> LoggedIn:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    override(get_sec_client, lambda: sec_client_with(facts={CIK: companyfacts()}))
    assert analyst.client.get("/api/v1/companies/EXDV/fundamentals").status_code == 200
    return analyst


def test_markets_overview_shows_stored_prices_and_curve(seeded: LoggedIn, db: Session) -> None:
    _bars(db, "SPY", 400, 0.0005)
    fetch = _fetch(db, "fred")
    db.add(MacroSeries(series_id="UNRATE", title="Unemployment Rate", units="Percent",
                       frequency="Monthly", source="BLS", release_lag_days=40))  # fmt: skip
    db.flush()
    for month, value in ((1, 3.9), (2, 4.0)):
        observed = date(2026, month, 1)
        db.add(MacroObservation(series_id="UNRATE", observation_date=observed,
                                realtime_start=observed + timedelta(days=36),
                                realtime_end=date(9999, 12, 31),
                                available_on=observed + timedelta(days=36),
                                value=Decimal(str(value)), fetch_id=fetch.id))  # fmt: skip
    db.commit()
    body = seeded.client.get("/api/v1/markets/overview").json()
    unrate = next(m for m in body["macro"] if m["series_id"] == "UNRATE")
    assert unrate["available"] is True and unrate["value"] == 4.0
    assert [p["value"] for p in unrate["history"]] == [3.9, 4.0]

    spy = next(e for e in body["etfs"] if e["ticker"] == "SPY")
    assert spy["close"] is not None and spy["returns"]["1y"] is not None
    assert next(e for e in body["etfs"] if e["ticker"] == "XLK")["close"] is None  # not loaded
    assert body["macro_configured"] is False
    assert body["regime"]["label"] in {"Uptrend, calm", "Uptrend, stressed"}
    assert body["curve"] is None and body["bitcoin"] is None
    assert any("internal use" in n for n in body["notes"])


def test_bitcoin_page_analysis(seeded: LoggedIn, db: Session) -> None:
    assert seeded.client.get("/api/v1/bitcoin").json()["available"] is False
    fetch = _fetch(db)
    start = date(2016, 6, 1)
    for i in range(1000):
        price = Decimal(str(round(600 * (1.002**i), 4)))
        db.add(
            CryptoPrice(
                pair="btcusd",
                trade_date=start + timedelta(days=i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal(1),
                fetch_id=fetch.id,
            )
        )
    db.commit()
    _bars(db, "SPY", 600, 0.0004, start=date(2016, 6, 1))

    body = seeded.client.get("/api/v1/bitcoin").json()

    assert body["available"] is True and len(body["series"]) == 1000
    assert body["summary"]["drawdown"] == pytest.approx(0)
    halving = body["halvings"][0]
    assert halving["halving"] == "2016-07-09" and halving["return_1y"] == pytest.approx(
        1.002**365 - 1, rel=1e-3
    )
    spy = next(r for r in body["relationships"] if r["name"].startswith("S&P"))
    assert spy["observations"] == 90


def test_technicals_adjust_for_splits(seeded: LoggedIn, db: Session) -> None:
    _bars(db, "SPY", 300, 0.0003)
    _bars(db, "EXDV", 300, 0.001)
    security = db.scalar(select(Security).where(Security.ticker == "EXDV"))
    last = db.scalars(
        select(DailyPrice)
        .where(DailyPrice.security_id == security.id)
        .order_by(DailyPrice.trade_date.desc())  # type: ignore[union-attr]
    ).first()
    assert last is not None
    # A 2-for-1 split on the last day: the raw close halves, the adjusted path does not jump.
    last.close = last.close / 2
    last.split_factor = Decimal(2)
    db.commit()

    body = seeded.client.get("/api/v1/companies/EXDV/technicals", params={"years": 1}).json()

    closes = [p["close"] for p in body["series"]]
    assert body["available"] is True
    assert closes[-1] == pytest.approx(float(last.close))
    assert closes[-1] / closes[-2] == pytest.approx(1.001 + 0.004 * math.sin(299), rel=1e-3)
    assert body["stats"]["beta_1y"] is not None and body["series"][-1]["relative"] is not None


def test_ownership_compares_quarters(seeded: LoggedIn, db: Session) -> None:
    company = db.scalar(select(Company).where(Company.cik == CIK))
    assert company is not None
    fetch = _fetch(db, "sec_edgar")
    q1, q2 = date(2026, 3, 31), date(2026, 6, 30)
    for period, holders in ((q1, {1: 100, 2: 50, 3: 10}), (q2, {1: 150, 2: 40, 4: 5})):
        db.add(
            OwnershipSummary(
                company_id=company.id,
                period_of_report=period,
                holders=len(holders),
                shares=Decimal(sum(holders.values())),
                value_usd=Decimal(1000),
                cusips=["123456789"],
                dataset="test.zip",
                fetch_id=fetch.id,
            )
        )
        for cik, shares in holders.items():
            db.add(
                InstitutionalHolding(
                    company_id=company.id,
                    period_of_report=period,
                    filer_cik=cik,
                    filer_name=f"Manager {cik}",
                    accession=f"A{cik}",
                    filing_date=period + timedelta(days=40),
                    shares=Decimal(shares),
                    value_usd=Decimal(shares * 10),
                    fetch_id=fetch.id,
                )
            )
    db.commit()

    body = seeded.client.get("/api/v1/companies/EXDV/ownership").json()

    assert [p["period"] for p in body["periods"]] == ["2026-06-30", "2026-03-31"]
    changes = {h["filer_cik"]: h["change"] for h in body["holders"]}
    assert changes == {1: "increased", 2: "decreased", 4: "entered_top"}
    assert [h["filer_cik"] for h in body["sold_out"]] == [3]


def test_models_and_predictions_endpoints(seeded: LoggedIn, db: Session) -> None:
    company = db.scalar(select(Company).where(Company.cik == CIK))
    assert company is not None
    version = ModelVersion(
        name="excess_21d",
        target="P(beats SPY over 21 trading days)",
        horizon_days=21,
        feature_set_version="f1",
        universe_version="u1",
        code_version="m1",
        trained_from=date(2015, 1, 31),
        trained_through=date(2026, 7, 31),
        feature_names=["mom_12_1"],
        params={},
        evaluation={
            "summary": {
                "model": {"auc": 0.53, "rank_ic": {"mean": 0.02}},
                "baseline": {"auc": 0.51},
            }
        },
        calibration={"method": "isotonic"},
        importance={"shap": []},
        artifact="{}",
    )
    db.add(version)
    db.add(
        UniverseMember(
            version="u1",
            as_of=date(2026, 9, 30),
            company_id=company.id,
            rank=1,
            revenue_ttm=Decimal(1),
            revenue_basis="FY",
            has_prices=True,
        )
    )
    db.flush()
    prediction = Prediction(
        model_version_id=version.id,
        company_id=company.id,
        as_of=date(2026, 8, 31),
        horizon_days=21,
        probability_raw=0.58,
        probability=0.55,
        expected_excess=0.004,
        excess_low=-0.06,
        excess_high=0.07,
        drivers=[{"feature": "mom_12_1", "value": 0.3, "percentile": 0.9, "contribution": 0.12}],
        data_available_on=date(2026, 8, 31),
    )
    db.add(prediction)
    db.flush()
    db.add(
        PredictionOutcome(
            prediction_id=prediction.id,
            end_date=date(2026, 9, 30),
            excess_return=0.01,
            went_up=True,
            brier=0.2025,
        )
    )
    db.commit()

    models = seeded.client.get("/api/v1/models").json()
    detail = seeded.client.get(f"/api/v1/models/{version.id}").json()
    body = seeded.client.get("/api/v1/companies/EXDV/predictions").json()

    assert models[0]["oos"]["auc"] == 0.53 and models[0]["live"]["scored"] == 1
    assert models[0]["live"]["brier"] == pytest.approx(0.2025)
    assert detail["calibration"]["method"] == "isotonic"
    assert seeded.client.get(f"/api/v1/models/{uuid.uuid4()}").status_code == 404
    assert body["in_universe"] is True
    assert body["predictions"][0]["probability"] == 0.55
    # AUC 0.53 alone is not enough: no Brier or rank-IC evidence, so not validated.
    assert body["predictions"][0]["validated"] is False
    assert models[0]["oos"]["validated"] is False
    assert body["predictions"][0]["drivers"][0]["feature"] == "mom_12_1"
    assert body["history"][0]["realised_excess"] == 0.01
    assert "not advice" in body["disclaimer"]


class CountingProvider:
    info = INFO

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_daily_bars(self, ticker: str, start: date, end: date) -> FetchResult[list[DailyBar]]:
        self.calls.append(ticker)
        meta = FetchMeta("tiingo", "daily_prices", f"https://example.test/{ticker}", "test")
        meta.finish(200, b"[]")
        bar = DailyBar(
            end - timedelta(days=3), Decimal(10), Decimal(11), Decimal(9), Decimal(10), 5
        )
        return FetchResult([bar], meta)


def test_price_loader_respects_the_hourly_budget(seeded: LoggedIn, db: Session) -> None:
    securities = list(
        db.scalars(select(Security).where(Security.ticker.in_(["SPY", "GLD", "EXDV"])))
    )
    provider = CountingProvider()
    settings = Settings(price_loader_requests_per_hour=2)  # type: ignore[call-arg]

    first = load_securities(db, provider, settings, securities, today=date(2026, 10, 2))  # type: ignore[arg-type]
    second = load_securities(db, provider, settings, securities, today=date(2026, 10, 2))  # type: ignore[arg-type]

    assert len(provider.calls) == 2 and first.out_of_budget and first.remaining == 1
    assert second.fetched == 0 and second.out_of_budget  # the hour's budget is spent
    assert db.scalar(select(func.count()).select_from(PriceCoverage)) == 2


def test_universe_ranks_by_revenue_known_at_each_date(seeded: LoggedIn, db: Session) -> None:
    company = db.scalar(select(Company).where(Company.cik == CIK))
    assert company is not None
    db.add(UniverseCandidate(company_id=company.id, best_rank=1, best_year=2024))
    db.commit()

    dates = [date(2023, 10, 31), date(2023, 12, 31), date(2024, 12, 31)]
    universe.rank_universe(db, Settings(), dates)  # type: ignore[call-arg]
    rows = {
        m.as_of: m
        for m in db.scalars(select(UniverseMember).where(UniverseMember.company_id == company.id))
    }

    assert date(2023, 10, 31) not in rows  # FY2023 10-K filed 2023-11-03
    assert rows[date(2023, 12, 31)].revenue_ttm == 1000  # as first reported
    assert rows[date(2024, 12, 31)].revenue_ttm == 1200
    assert rows[date(2024, 12, 31)].has_prices is False  # no price history loaded


def test_research_status_and_jobs_are_scheduled(seeded: LoggedIn, db: Session) -> None:
    from app.jobs.scheduler import tick

    tick(db, Settings(), utcnow())  # type: ignore[call-arg]
    kinds = set(db.scalars(select(Job.kind)))
    body = seeded.client.get("/api/v1/research/status").json()

    assert {
        "refresh_macro",
        "load_prices",
        "load_bitcoin",
        "update_research",
        "build_universe",
        "train_models",
        "refresh_ownership",
    } <= kinds
    assert body["fred_configured"] is False and body["models"] == 0
    assert body["price_targets"] >= 2  # SPY and GLD reference series


def test_treasury_history_loads_each_year_once(seeded: LoggedIn, db: Session) -> None:
    from app.providers.treasury import TreasuryClient

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        year = request.url.path.split("/")[-2]
        return httpx.Response(200, text=f'Date,"10 Yr"\n06/30/{year},4.0\n')

    client = TreasuryClient(httpx.Client(transport=httpx.MockTransport(handler)), "test")
    pipeline.refresh_treasury_history(db, client, first_year=2024, today=date(2026, 10, 2))
    assert len(calls) == 3
    pipeline.refresh_treasury_history(db, client, first_year=2024, today=date(2026, 10, 2))
    # Past years with few rows are retried; the current year is always refreshed.
    assert len(calls) == 6
