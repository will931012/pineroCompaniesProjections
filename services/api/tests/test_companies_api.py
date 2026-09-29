import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.companies.routes import get_sec_client
from app.db.models import Company, ProviderFetch, Security
from app.ingestion.sec_directory import sync_sec_directory
from tests.conftest import LoggedIn
from tests.fakes import sec_client_with, submissions_payload

pytestmark = pytest.mark.integration

ROWS = [
    [320193, "Apple Inc.", "AAPL", "Nasdaq"],
    [1045810, "NVIDIA CORP", "NVDA", "Nasdaq"],
    [1018724, "AMAZON COM INC", "AMZN", "Nasdaq"],
    [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"],
    [1067983, "BERKSHIRE HATHAWAY INC", "BRK-A", "NYSE"],
    [2488, "ADVANCED MICRO DEVICES INC", "AMD", "Nasdaq"],
]


def test_directory_sync_records_provenance_and_is_idempotent(db: Session) -> None:
    first = sync_sec_directory(db, sec_client_with(directory_rows=ROWS))
    second = sync_sec_directory(db, sec_client_with(directory_rows=ROWS))

    assert first.companies_created == 5 and first.securities_created == 6
    assert second.companies_created == 0 and second.securities_created == 0
    assert db.scalar(select(Company).where(Company.cik == 1067983)) is not None
    fetch = db.get(ProviderFetch, second.fetch_id)
    assert fetch is not None and fetch.provider == "sec_edgar" and fetch.status == "success"
    assert fetch.record_count == 6 and fetch.content_sha256


def test_reassigned_ticker_keeps_history(db: Session) -> None:
    sync_sec_directory(db, sec_client_with(directory_rows=ROWS))
    moved = [r for r in ROWS if r[2] != "AMD"] + [[999999, "NEW REGISTRANT", "AMD", "Nasdaq"]]

    summary = sync_sec_directory(db, sec_client_with(directory_rows=moved))

    assert summary.securities_reassigned == 1
    listings = db.scalars(select(Security).where(Security.ticker == "AMD")).all()
    assert sorted((s.is_active, s.company.cik) for s in listings) == [(False, 2488), (True, 999999)]


def test_delisted_securities_are_deactivated_not_deleted(db: Session) -> None:
    sync_sec_directory(db, sec_client_with(directory_rows=ROWS))

    summary = sync_sec_directory(db, sec_client_with(directory_rows=ROWS[:-1]))

    assert summary.securities_deactivated == 1
    amd = db.scalar(select(Security).where(Security.ticker == "AMD"))
    assert amd is not None and amd.is_active is False


def test_partial_snapshot_does_not_mass_deactivate(db: Session) -> None:
    sync_sec_directory(db, sec_client_with(directory_rows=ROWS))

    summary = sync_sec_directory(db, sec_client_with(directory_rows=ROWS[:2]))

    assert summary.deactivation_skipped is True
    assert summary.securities_deactivated == 0


def test_failed_sync_is_recorded(db: Session) -> None:
    from app.providers.base import ProviderError

    with pytest.raises(ProviderError):
        sync_sec_directory(db, sec_client_with(status=403))

    fetch = db.scalar(select(ProviderFetch))
    assert fetch is not None and fetch.status == "error"
    assert fetch.error_code == "provider_access_denied"


def test_search_ranks_exact_ticker_first(analyst: LoggedIn, seed_directory) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)

    by_ticker = analyst.client.get("/api/v1/companies", params={"query": "amd"}).json()
    by_name = analyst.client.get("/api/v1/companies", params={"query": "berkshire"}).json()
    literal = analyst.client.get("/api/v1/companies", params={"query": "%"}).json()

    assert by_ticker["items"][0]["ticker"] == "AMD"
    assert {i["ticker"] for i in by_name["items"]} == {"BRK-A", "BRK-B"}
    assert by_name["total"] == 2
    assert literal["total"] == 0  # wildcards are escaped


def test_profile_refreshes_from_sec_submissions(
    analyst: LoggedIn, seed_directory, override
) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    calls: list = []
    client = sec_client_with(
        submissions={320193: submissions_payload(320193, name="Apple Inc.")}, calls=calls
    )
    override(get_sec_client, lambda: client)

    first = analyst.client.get("/api/v1/companies/aapl").json()
    second = analyst.client.get("/api/v1/companies/AAPL").json()

    assert first["profile_status"] == "current"
    assert first["classification"] == {
        "system": "SIC",
        "code": "3571",
        "sector": "Manufacturing",
        "industry": "Electronic Computers",
    }
    assert first["fiscal_year_end"] == "0926"
    assert first["headquarters"] == "CUPERTINO, CA"
    assert {s["dataset"] for s in first["sources"]} == {"company_tickers_exchange", "submissions"}
    assert first["availability"]["market_data"]["status"] == "not_configured"
    assert second["profile_status"] == "current"
    assert len(calls) == 1  # second request served within the profile TTL


def test_profile_without_sec_user_agent_says_so(
    analyst: LoggedIn, seed_directory, override
) -> None:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    override(get_sec_client, lambda: sec_client_with(user_agent=None))

    body = analyst.client.get("/api/v1/companies/NVDA").json()

    assert body["profile_status"] == "not_configured"
    assert "SEC_USER_AGENT" in body["profile_message"]
    assert body["classification"] is None
    assert body["name"] == "NVIDIA CORP"


def test_unknown_company_is_not_invented(analyst: LoggedIn) -> None:
    response = analyst.client.get("/api/v1/companies/UNKNOWN")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "company_not_found"


def test_admin_can_trigger_directory_sync(admin: LoggedIn, override) -> None:  # type: ignore[no-untyped-def]
    override(get_sec_client, lambda: sec_client_with(directory_rows=ROWS))

    response = admin.client.post("/api/v1/admin/ingestion/sec-directory", headers=admin.headers())
    status = admin.client.get("/api/v1/system/status").json()

    assert response.status_code == 200 and response.json()["securities_created"] == 6
    assert status["directory"]["active_securities"] == 6
    assert status["directory"]["last_synced_at"] is not None
