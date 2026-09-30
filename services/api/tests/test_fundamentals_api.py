import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.companies.routes import get_sec_client
from app.core.config import Settings, get_settings
from app.db.models import Company, CompanyMetric, FinancialFact, ProviderFetch
from app.market_data.routes import configured_provider
from tests.conftest import LoggedIn
from tests.fakes import (
    json_response,
    sec_client_with,
    submissions_payload,
    tiingo_row,
    tiingo_with,
)
from tests.fixtures_companyfacts import CIK, companyfacts

pytestmark = pytest.mark.integration

PEER_CIK = 999002
ROWS = [
    [CIK, "Example Devices Inc.", "EXDV", "Nasdaq"],
    [PEER_CIK, "Example Peer Corp", "EXPR", "NYSE"],
    [999003, "No Filings Inc", "NOFL", "NYSE"],
]
FACTS = {CIK: companyfacts(), PEER_CIK: companyfacts(scale=2)}


@pytest.fixture
def sec(override, seed_directory):  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    calls: list[httpx.Request] = []
    client = sec_client_with(facts=FACTS, calls=calls)
    override(get_sec_client, lambda: client)
    return calls


def test_fundamentals_are_loaded_once_with_provenance(
    analyst: LoggedIn,
    sec,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    first = analyst.client.get("/api/v1/companies/EXDV/fundamentals").json()
    second = analyst.client.get("/api/v1/companies/EXDV/fundamentals").json()

    assert first["status"] == "current" and first["cik"] == CIK
    assert [p["key"] for p in first["periods"]] == ["FY2023", "FY2024"]
    revenue = next(i for i in first["statements"]["income"] if i["key"] == "revenue")
    fy24 = next(c for c in revenue["cells"] if c["period_key"] == "FY2024")
    assert fy24 == {
        "period_key": "FY2024",
        "value": 1200.0,
        "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "accession": "0000999001-24-000040",
        "filed_date": "2024-11-01",
        "derivation": None,
    }
    roe = next(m for m in first["metrics"] if m["key"] == "roe")
    assert roe["formula"] and roe["values"][0]["period_key"] == "FY2024"
    assert first["sources"][0]["dataset"] == "companyfacts"
    assert second["status"] == "current"
    facts_calls = [c for c in sec if "companyfacts" in c.url.path]
    assert len(facts_calls) == 1  # second request served within the TTL
    assert db.scalar(select(func.count()).select_from(FinancialFact)) > 0


def test_quarterly_view_marks_derived_quarters(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    body = analyst.client.get(
        "/api/v1/companies/EXDV/fundamentals", params={"period": "quarterly"}
    ).json()

    ocf = next(i for i in body["statements"]["cash_flow"] if i["key"] == "operating_cash_flow")
    q4 = next(c for c in ocf["cells"] if c["period_key"] == "FY2024-Q4")
    assert q4["value"] == 90.0 and q4["derivation"] == "FY − 9M YTD"


def test_point_in_time_request_reads_stored_facts_only(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    analyst.client.get("/api/v1/companies/EXDV/fundamentals")  # load
    body = analyst.client.get(
        "/api/v1/companies/EXDV/fundamentals", params={"as_of": "2023-12-31"}
    ).json()

    assert body["as_of"] == "2023-12-31"
    assert [p["key"] for p in body["periods"]] == ["FY2023"]
    revenue = next(i for i in body["statements"]["income"] if i["key"] == "revenue")
    assert revenue["cells"][0]["value"] == 1000.0  # before the 10-K/A restatement


def test_company_without_xbrl_data_says_so(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    body = analyst.client.get("/api/v1/companies/NOFL/fundamentals").json()

    assert body["status"] == "not_available"
    assert body["periods"] == [] and body["metrics"] == []


def test_without_sec_user_agent_nothing_is_fetched(
    analyst: LoggedIn,
    override,
    seed_directory,  # type: ignore[no-untyped-def]
) -> None:
    seed_directory(ROWS)
    override(get_sec_client, lambda: sec_client_with(user_agent=None))

    body = analyst.client.get("/api/v1/companies/EXDV/fundamentals").json()

    assert body["status"] == "not_configured"
    assert "SEC_USER_AGENT" in body["message"]


def test_snapshot_metrics_state_their_basis(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    analyst.client.get("/api/v1/companies/EXDV/fundamentals")

    body = analyst.client.get("/api/v1/companies/EXDV/metrics").json()
    metrics = {m["key"]: m for m in body["metrics"]}

    assert metrics["revenue"]["value"] == 1200.0 and metrics["revenue"]["basis"] == "TTM Q4 FY2024"
    assert metrics["roic"]["basis"] == "FY2024"
    assert "market_cap" not in metrics  # no stored prices yet
    assert body["formula_version"]


def test_prices_add_market_metrics(
    analyst: LoggedIn,
    sec,
    override,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    analyst.client.get("/api/v1/companies/EXDV/fundamentals")
    provider = tiingo_with(
        lambda _: json_response([tiingo_row("2024-10-30", 50.0), tiingo_row("2024-10-31", 52.0)])
    )
    override(configured_provider, lambda: provider)
    override(get_settings, lambda: Settings(market_data_provider="tiingo", tiingo_api_key="k"))  # type: ignore[arg-type]

    analyst.client.get(
        "/api/v1/market-data/EXDV/bars", params={"from": "2024-10-01", "to": "2024-10-31"}
    )
    metrics = {
        m["key"]: m for m in analyst.client.get("/api/v1/companies/EXDV/metrics").json()["metrics"]
    }

    assert metrics["market_cap"]["value"] == 52.0 * 60
    assert metrics["pe_ratio"]["value"] == pytest.approx(52.0 * 60 / 150)
    assert metrics["market_cap"]["price_based"] is True


def test_screener_filters_and_sorts(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    for ticker in ("EXDV", "EXPR"):
        analyst.client.get(f"/api/v1/companies/{ticker}/fundamentals")

    everything = analyst.client.post(
        "/api/v1/screener",
        headers=analyst.headers(),
        json={"sort_by": "revenue", "columns": ["revenue", "roic"]},
    ).json()
    large = analyst.client.post(
        "/api/v1/screener",
        headers=analyst.headers(),
        json={"filters": [{"metric": "revenue", "min": 2000}]},
    ).json()
    both_filters = analyst.client.post(
        "/api/v1/screener",
        headers=analyst.headers(),
        json={
            "filters": [
                {"metric": "revenue", "min": 1000},
                {"metric": "current_ratio", "max": 1.4},
            ],
        },
    ).json()

    assert [r["ticker"] for r in everything["rows"]] == ["EXPR", "EXDV"]
    assert everything["rows"][0]["metrics"]["revenue"] == 2400.0
    assert everything["universe"] == 2
    assert [r["ticker"] for r in large["rows"]] == ["EXPR"] and large["total"] == 1
    assert both_filters["total"] == 0  # current ratio is 1.5 for both


def test_screener_validates_metrics(analyst: LoggedIn) -> None:
    bad_filter = analyst.client.post(
        "/api/v1/screener",
        headers=analyst.headers(),
        json={"filters": [{"metric": "made_up", "min": 1}]},
    )
    bad_range = analyst.client.post(
        "/api/v1/screener",
        headers=analyst.headers(),
        json={"filters": [{"metric": "roic", "min": 2, "max": 1}]},
    )
    bad_sort = analyst.client.post(
        "/api/v1/screener", headers=analyst.headers(), json={"sort_by": "made_up"}
    )

    assert bad_filter.status_code == bad_range.status_code == 422
    assert bad_sort.json()["error"]["code"] == "unknown_metric"
    catalog = analyst.client.get("/api/v1/screener/metrics").json()
    assert {"roic", "fcf_yield", "momentum_6m"} <= {m["key"] for m in catalog}


def test_peers_by_sic_code(analyst: LoggedIn, sec, db: Session) -> None:  # type: ignore[no-untyped-def]
    for ticker in ("EXDV", "EXPR"):
        analyst.client.get(f"/api/v1/companies/{ticker}/fundamentals")
    db.execute(update(Company).where(Company.cik.in_([CIK, PEER_CIK])).values(sic_code="3571"))
    db.commit()

    body = analyst.client.get("/api/v1/companies/EXDV/peers").json()

    assert body["basis"] == "SIC major group 35"  # fewer than 3 exact-SIC peers
    assert [r["ticker"] for r in body["rows"]] == ["EXDV", "EXPR"]
    assert body["rows"][0]["is_subject"] is True
    assert body["rows"][1]["metrics"]["revenue"] == 2400.0


def test_peers_accept_explicit_tickers(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    body = analyst.client.get("/api/v1/companies/EXDV/peers", params={"tickers": "NOFL"}).json()

    assert [r["ticker"] for r in body["rows"]] == ["EXDV", "NOFL"]


def test_admin_fundamentals_sync_is_audited(admin: LoggedIn, sec, db: Session) -> None:  # type: ignore[no-untyped-def]
    response = admin.client.post(
        "/api/v1/admin/ingestion/fundamentals",
        json={"tickers": ["exdv", "EXDV", "NOFL"]},
        headers=admin.headers(),
    )

    assert response.status_code == 200
    assert [(r["ticker"], r["status"]) for r in response.json()] == [
        ("EXDV", "current"),
        ("NOFL", "not_available"),
    ]
    assert db.scalar(select(func.count()).select_from(CompanyMetric)) > 0
    datasets = set(db.scalars(select(ProviderFetch.dataset)))
    assert "companyfacts" in datasets
    events = admin.client.get("/api/v1/admin/audit-events").json()
    assert events[0]["action"] == "admin.fundamentals_sync"


def test_bulk_sync_loads_sec_profiles_for_peer_selection(
    admin: LoggedIn,
    override,  # type: ignore[no-untyped-def]
    seed_directory,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    seed_directory(ROWS)
    client = sec_client_with(facts=FACTS, submissions={PEER_CIK: submissions_payload(PEER_CIK)})
    override(get_sec_client, lambda: client)

    admin.client.post(
        "/api/v1/admin/ingestion/fundamentals", json={"tickers": ["EXPR"]}, headers=admin.headers()
    )

    # Nobody opened the company page, yet the SIC code needed for peers is present.
    assert db.scalar(select(Company.sic_code).where(Company.cik == PEER_CIK)) == "3571"
