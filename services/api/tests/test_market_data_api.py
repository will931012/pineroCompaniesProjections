import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import DailyPrice, ProviderFetch
from app.market_data.routes import configured_provider
from tests.conftest import LoggedIn
from tests.fakes import TEST_TIINGO_KEY, json_response, tiingo_row, tiingo_with

pytestmark = pytest.mark.integration

ROWS = [[320193, "Apple Inc.", "AAPL", "Nasdaq"]]
PARAMS = {"from": "2026-01-01", "to": "2026-01-31"}
TIINGO_SETTINGS = Settings(market_data_provider="tiingo", tiingo_api_key=SecretStr("k"))


@pytest.fixture
def seeded(seed_directory) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)


def use_tiingo(override, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    provider = tiingo_with(recording)
    override(configured_provider, lambda: provider)
    override(get_settings, lambda: TIINGO_SETTINGS)
    return calls


def test_fails_closed_without_provider(analyst: LoggedIn, seeded: None) -> None:
    response = analyst.client.get("/api/v1/market-data/AAPL/bars", params=PARAMS)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert "bars" not in response.json()


def test_rejects_reversed_date_range(analyst: LoggedIn, seeded: None) -> None:
    response = analyst.client.get(
        "/api/v1/market-data/AAPL/bars", params={"from": "2026-02-01", "to": "2026-01-01"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date_range"


def test_fresh_fetch_is_persisted_with_provenance_then_cached(
    analyst: LoggedIn,
    seeded: None,
    override,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    rows = [tiingo_row("2026-01-02", 100.0), tiingo_row("2026-01-05", 102.0)]
    calls = use_tiingo(override, lambda _: json_response(rows))

    fresh = analyst.client.get("/api/v1/market-data/aapl/bars", params=PARAMS).json()
    cached = analyst.client.get("/api/v1/market-data/AAPL/bars", params=PARAMS).json()

    assert fresh["quality"]["status"] == "fresh"
    assert [b["close"] for b in fresh["bars"]] == [100.0, 102.0]
    assert fresh["summary"] == {
        "as_of": "2026-01-05",
        "last_close": 102.0,
        "previous_close": 100.0,
        "change": 2.0,
        "change_percent": 2.0,
        "basis": "adjusted",
    }
    source = fresh["sources"][0]
    assert source["provider"] == "tiingo" and TEST_TIINGO_KEY not in source["source_url"]
    assert all(b["fetch_id"] == source["fetch_id"] for b in fresh["bars"])
    assert cached["quality"]["status"] == "cached"
    assert len(calls) == 1
    assert db.scalar(select(func.count()).select_from(DailyPrice)) == 2


def test_rejected_rows_are_reported(analyst: LoggedIn, seeded: None, override) -> None:  # type: ignore[no-untyped-def]
    rows = [tiingo_row("2026-01-02", 100.0), tiingo_row("2026-01-05", 100.0, low=150.0)]
    use_tiingo(override, lambda _: json_response(rows))

    body = analyst.client.get("/api/v1/market-data/AAPL/bars", params=PARAMS).json()

    assert len(body["bars"]) == 1
    assert body["quality"]["rejected_count"] == 1
    assert body["quality"]["warnings"]


def test_provider_failure_falls_back_to_stored_bars_with_warning(
    analyst: LoggedIn,
    seeded: None,
    override,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    use_tiingo(override, lambda _: json_response([tiingo_row("2026-01-02", 100.0)]))
    analyst.client.get("/api/v1/market-data/AAPL/bars", params=PARAMS)
    wider = {"from": "2025-12-01", "to": "2026-01-31"}  # not covered by the cached fetch
    use_tiingo(override, lambda _: httpx.Response(503))

    body = analyst.client.get("/api/v1/market-data/AAPL/bars", params=wider).json()

    assert body["quality"]["status"] == "stale"
    assert "provider_unavailable" in body["quality"]["warnings"][0]
    statuses = db.scalars(select(ProviderFetch.status).where(ProviderFetch.provider == "tiingo"))
    assert sorted(statuses) == ["error", "success"]


def test_symbol_not_found_maps_to_404(analyst: LoggedIn, seeded: None, override) -> None:  # type: ignore[no-untyped-def]
    use_tiingo(override, lambda _: httpx.Response(404))

    response = analyst.client.get("/api/v1/market-data/AAPL/bars", params=PARAMS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "provider_symbol_not_found"


def test_unknown_ticker_is_rejected_before_calling_provider(
    analyst: LoggedIn,
    seeded: None,
    override,  # type: ignore[no-untyped-def]
) -> None:
    calls = use_tiingo(override, lambda _: json_response([]))

    response = analyst.client.get("/api/v1/market-data/ZZZZ/bars", params=PARAMS)

    assert response.status_code == 404
    assert calls == []
