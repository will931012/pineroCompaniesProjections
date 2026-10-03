from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts import routes as alert_routes
from app.companies.routes import get_sec_client
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.models import Alert, Company, Event, Filing, InsiderTransaction, Job, NewsItem
from app.events.routes import get_gdelt
from app.jobs import handlers as job_handlers
from app.jobs.queue import claim, complete, enqueue, fail
from app.jobs.scheduler import tick
from app.providers.email import EmailError
from app.providers.gdelt import GdeltClient
from app.worker import run_one
from tests.conftest import LoggedIn
from tests.fakes import sec_client_with, submissions_payload
from tests.fixtures_filings import CIK, DOCUMENTS, PAGES, filings_block
from tests.fixtures_news import gdelt_payload

pytestmark = pytest.mark.integration

ROWS = [[CIK, "Example Devices Inc.", "EXDV", "Nasdaq"]]
EARNINGS_8K = "0000999001-24-000035"


class FakeSender:
    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, to: str, subject: str, text: str, html: str) -> str:
        if self.fail:
            raise EmailError("Resend returned HTTP 403: domain not verified")
        self.sent.append((to, subject))
        return "msg_1"


@pytest.fixture
def sources(override, seed_directory) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    calls: list[httpx.Request] = []
    sec = sec_client_with(
        submissions={CIK: submissions_payload(CIK, filings=filings_block())},
        documents=DOCUMENTS,
        pages=PAGES,
    )
    override(get_sec_client, lambda: sec)

    def gdelt_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=gdelt_payload())

    gdelt = GdeltClient(
        httpx.Client(transport=httpx.MockTransport(gdelt_handler)), "test", min_interval=0
    )
    override(get_gdelt, lambda: gdelt)
    return calls


def _refresh(user: LoggedIn) -> dict[str, Any]:
    response = user.client.post("/api/v1/companies/EXDV/news/refresh", headers=user.headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_news_refresh_links_classifies_and_clusters(analyst: LoggedIn, sources) -> None:  # type: ignore[no-untyped-def]
    first = _refresh(analyst)
    assert (first["status"], first["articles"], first["new"], first["events"]) == (
        "current",
        4,
        4,
        3,
    )
    assert "sourcelang:english" in str(sources[0].url.params["query"])

    body = analyst.client.get("/api/v1/companies/EXDV/news").json()
    assert body["low_confidence_hidden"] == 1 and "GDELT" in body["attribution"]
    items = {i["title"]: i for i in body["items"]}
    results = items["Example Devices (NASDAQ: EXDV) fourth-quarter results beat estimates"]
    assert (results["link_method"], results["event"]["event_type"]) == ("title_ticker", "earnings")
    deal = items["Example Devices to acquire Widget Co for $2,000 million"]
    rewrite = items["Example Devices to buy Widget Co in $2,000 million deal"]
    assert deal["event"]["novelty"] == "first" and rewrite["event"]["novelty"] == "repeat"
    assert rewrite["event"]["cluster_id"] == deal["event"]["cluster_id"]
    assert deal["event"]["cluster_size"] == 2

    everything = analyst.client.get(
        "/api/v1/companies/EXDV/news", params={"confidence": "all"}
    ).json()
    assert len(everything["items"]) == 4

    again = _refresh(analyst)
    assert (again["new"], again["events"]) == (0, 0)  # the same articles are not stored twice


def test_events_timeline_merges_filings_and_first_reports(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
) -> None:
    analyst.client.get("/api/v1/companies/EXDV/filings")  # index the filings
    _refresh(analyst)

    body = analyst.client.get("/api/v1/companies/EXDV/events", params={"days": 3650}).json()
    kinds = {(e["source_kind"], e["event_type"]) for e in body["events"]}
    assert ("filing", "earnings") in kinds and ("news", "m_and_a") in kinds
    filing_event = next(e for e in body["events"] if e["source_kind"] == "filing")
    assert filing_event["filing"]["accession"] == EARNINGS_8K
    assert filing_event["evidence"]["items"] == ["2.02", "9.01"]
    assert not any(e["novelty"] == "repeat" for e in body["events"])
    deal = next(e for e in body["events"] if e["event_type"] == "m_and_a")
    assert sorted(deal["outlets"]) == ["daily.example.net", "wire.example.org"]

    with_repeats = analyst.client.get(
        "/api/v1/companies/EXDV/events", params={"days": 3650, "include_repeats": True}
    ).json()
    assert len(with_repeats["events"]) == len(body["events"]) + 1


def test_earnings_release_includes_the_press_release_exhibit(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
) -> None:
    analyst.client.get(f"/api/v1/companies/EXDV/filings/{EARNINGS_8K}")  # loads the 8-K
    body = analyst.client.get("/api/v1/companies/EXDV/earnings").json()

    release = body["releases"][0]
    assert release["filing"]["accession"] == EARNINGS_8K
    assert release["press_release_excerpt"].startswith("Example Devices Reports Fourth Quarter")
    detail = analyst.client.get(f"/api/v1/companies/EXDV/filings/{EARNINGS_8K}").json()
    assert [s["key"] for s in detail["sections"]][-1] == "exhibit_99"


def test_alert_rules_fire_once_and_record_email_outcome(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = analyst.client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "EXDV deals",
            "kind": "news",
            "tickers": ["exdv"],
            "params": {"event_types": ["m_and_a"]},
        },
        headers=analyst.headers(),
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert (rule["tickers"], rule["companies"]) == (["EXDV"], 1)

    _refresh(analyst)
    sender = FakeSender()
    monkeypatch.setattr(alert_routes, "get_email_sender", lambda settings: sender)
    evaluated = analyst.client.post(
        f"/api/v1/alerts/rules/{rule['id']}/evaluate", headers=analyst.headers()
    ).json()
    # Only the first report of the deal fires; the rewrite is repeat coverage.
    assert evaluated == {"created": 1, "sent": 1, "failed": 0}
    assert sender.sent == [("analyst@example.com", "[Pinero] EXDV: Mergers & acquisitions")]

    alerts = analyst.client.get("/api/v1/alerts").json()
    assert alerts["unread"] == 1 and alerts["alerts"][0]["email_status"] == "sent"
    again = analyst.client.post(
        f"/api/v1/alerts/rules/{rule['id']}/evaluate", headers=analyst.headers()
    ).json()
    assert again["created"] == 0
    analyst.client.post("/api/v1/alerts/read", json={}, headers=analyst.headers())
    assert analyst.client.get("/api/v1/alerts").json()["unread"] == 0


def test_email_failures_and_missing_configuration_are_visible(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = analyst.client.post(
        "/api/v1/alerts/rules",
        json={"name": "All EXDV news", "kind": "news", "tickers": ["EXDV"]},
        headers=analyst.headers(),
    ).json()
    _refresh(analyst)
    monkeypatch.setattr(alert_routes, "get_email_sender", lambda settings: FakeSender(fail=True))
    outcome = analyst.client.post(
        f"/api/v1/alerts/rules/{rule['id']}/evaluate", headers=analyst.headers()
    ).json()
    assert outcome["created"] == 2 and outcome["failed"] == 2
    statuses = {a["email_status"] for a in analyst.client.get("/api/v1/alerts").json()["alerts"]}
    assert statuses == {"failed"}

    monkeypatch.setattr(alert_routes, "get_email_sender", lambda settings: None)
    status = analyst.client.get("/api/v1/alerts/status").json()
    assert status["email_configured"] is False and status["recipient"] == "analyst@example.com"
    test = analyst.client.post("/api/v1/alerts/test-email", headers=analyst.headers())
    assert test.json()["error"]["code"] == "email_not_configured"


def test_old_filings_do_not_flood_new_rules_but_recent_insider_buys_fire(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    analyst.client.get("/api/v1/companies/EXDV/insiders", params={"months": 120})
    filing_rule = analyst.client.post(
        "/api/v1/alerts/rules",
        json={"name": "EXDV filings", "kind": "filing", "tickers": ["EXDV"], "email": False},
        headers=analyst.headers(),
    ).json()
    insider_rule = analyst.client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "EXDV insider buys",
            "kind": "insider",
            "tickers": ["EXDV"],
            "params": {"codes": ["P"], "min_value": 10000},
        },
        headers=analyst.headers(),
    ).json()
    # The fixture filings are from 2024: nothing old fires for a rule created now.
    old = analyst.client.post(
        f"/api/v1/alerts/rules/{filing_rule['id']}/evaluate", headers=analyst.headers()
    ).json()
    assert old["created"] == 0

    # A Form 4 purchase reported now does fire (value 150 × $140 = $21,000 ≥ $10,000).
    company = db.scalar(select(Company).where(Company.cik == CIK))
    assert company is not None
    form4 = db.scalar(select(Filing).where(Filing.accession == "0000999001-24-000050"))
    assert form4 is not None
    form4.loaded_at = utcnow()
    db.add(
        InsiderTransaction(
            filing_id=form4.id,
            company_id=company.id,
            owners=[],
            owner_cik=1,
            owner_name="Roe Richard",
            is_director=True,
            is_officer=False,
            is_ten_percent_owner=False,
            is_derivative=False,
            ordinal=99,
            transaction_date=utcnow().date() - timedelta(days=1),
            transaction_code="P",
            shares=Decimal(150),
            price=Decimal(140),
            acquired_disposed="A",
        )
    )
    db.commit()
    fired = analyst.client.post(
        f"/api/v1/alerts/rules/{insider_rule['id']}/evaluate", headers=analyst.headers()
    ).json()
    assert fired["created"] == 1
    alert = db.scalar(select(Alert).where(Alert.rule_id == insider_rule["id"]))
    assert alert is not None and alert.title == "EXDV: insider bought $21,000"
    assert alert.email_status == "not_configured"


def test_alert_rules_are_validated_and_private(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
    make_user: Callable[..., str],
    login: Callable[[str], LoggedIn],
) -> None:
    def create(body: dict[str, Any]) -> httpx.Response:
        return analyst.client.post("/api/v1/alerts/rules", json=body, headers=analyst.headers())

    assert create({"name": "x", "kind": "news", "tickers": ["NOPE"]}).json()["error"]["code"] == (
        "unknown_ticker"
    )
    assert create({"name": "x", "kind": "news", "tickers": []}).status_code == 422
    bad_type = create(
        {"name": "x", "kind": "news", "tickers": ["EXDV"], "params": {"event_types": ["gossip"]}}
    )
    assert bad_type.status_code == 422
    rule = create({"name": "mine", "kind": "earnings", "tickers": ["EXDV"]}).json()

    other = login(make_user("other@example.com", "viewer"))
    assert other.client.get("/api/v1/alerts/rules").json() == []
    stolen = other.client.delete(f"/api/v1/alerts/rules/{rule['id']}", headers=other.headers())
    assert stolen.status_code == 404


def test_queue_claims_once_retries_with_backoff_and_recovers_stale_jobs(db: Session) -> None:
    assert enqueue(db, "poll_news", dedupe_key="poll_news:1") is not None
    assert enqueue(db, "poll_news", dedupe_key="poll_news:1") is None  # already queued

    job = claim(db, "worker-a")
    assert job is not None and (job.status, job.attempts, job.locked_by) == (
        "running",
        1,
        "worker-a",
    )
    assert claim(db, "worker-b") is None  # nothing else is due

    fail(db, job, "GDELT timed out")
    assert job.status == "queued" and job.run_at > utcnow()  # retried later, not now
    job.run_at = utcnow()
    db.commit()
    again = claim(db, "worker-b")
    assert again is not None and again.id == job.id and again.attempts == 2
    complete(db, again, {"ok": True})
    assert again.status == "done"
    # A finished job frees the dedupe key.
    assert enqueue(db, "poll_news", dedupe_key="poll_news:1") is not None

    crashed = claim(db, "worker-c")
    assert crashed is not None
    crashed.locked_at = utcnow() - timedelta(hours=1)
    db.commit()
    recovered = claim(db, "worker-d")
    assert recovered is not None and recovered.id == crashed.id


def test_jobs_give_up_after_max_attempts(db: Session) -> None:
    enqueue(db, "evaluate_alerts", max_attempts=1)
    job = claim(db, "w")
    assert job is not None
    fail(db, job, "boom")
    assert job.status == "failed" and job.finished_at is not None


def test_scheduler_enqueues_each_period_once(db: Session) -> None:
    now = utcnow()
    first = tick(db, get_settings(), now)
    assert {k.split(":")[0] for k in first} == {
        "refresh_filings",
        "poll_news",
        "evaluate_alerts",
        "refresh_prices",
        # Phase 6
        "refresh_macro",
        "load_prices",
        "load_bitcoin",
        "update_research",
        "build_universe",
        "train_models",
        "refresh_ownership",
    }
    assert tick(db, get_settings(), now) == []
    for job in db.scalars(select(Job)):
        job.status = "done"
    db.commit()
    assert tick(db, get_settings(), now) == []  # done jobs still count for their period


def test_worker_runs_handlers_and_records_failures(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(job_handlers.HANDLERS, "ok", lambda db, ctx, payload: {"echo": payload})
    monkeypatch.setitem(job_handlers.HANDLERS, "broken", lambda db, ctx, payload: 1 / 0)  # type: ignore[arg-type,return-value]
    enqueue(db, "ok", {"n": 1})
    assert run_one(None, "test-worker") is True  # type: ignore[arg-type]
    enqueue(db, "broken")
    assert run_one(None, "test-worker") is True  # type: ignore[arg-type]
    assert run_one(None, "test-worker") is False

    db.expire_all()
    jobs = {j.kind: j for j in db.scalars(select(Job))}
    assert jobs["ok"].status == "done" and jobs["ok"].result == {"echo": {"n": 1}}
    assert jobs["broken"].status == "queued" and "ZeroDivisionError" in (
        jobs["broken"].last_error or ""
    )


def test_admin_can_queue_jobs_and_see_the_queue(admin: LoggedIn, sources) -> None:  # type: ignore[no-untyped-def]
    bad = admin.client.post(
        "/api/v1/admin/jobs", json={"kind": "mine_bitcoin"}, headers=admin.headers()
    )
    assert bad.json()["error"]["code"] == "unknown_job"
    queued = admin.client.post(
        "/api/v1/admin/jobs",
        json={"kind": "poll_news", "tickers": ["EXDV"]},
        headers=admin.headers(),
    )
    assert queued.status_code == 201 and queued.json()["status"] == "queued"
    overview = admin.client.get("/api/v1/admin/jobs").json()
    assert overview["counts"]["poll_news"]["queued"] == 1


def test_feed_shows_first_reports_for_watchlisted_companies(
    analyst: LoggedIn,
    sources,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    assert analyst.client.get("/api/v1/feed").json() == {"items": [], "companies": 0}
    watchlist = analyst.client.post(
        "/api/v1/watchlists", json={"name": "Core"}, headers=analyst.headers()
    ).json()
    analyst.client.post(
        f"/api/v1/watchlists/{watchlist['id']}/items",
        json={"ticker": "EXDV"},
        headers=analyst.headers(),
    )
    _refresh(analyst)
    feed = analyst.client.get("/api/v1/feed").json()
    assert feed["companies"] == 1
    assert {i["ticker"] for i in feed["items"]} == {"EXDV"}
    assert all(i["event"]["novelty"] == "first" for i in feed["items"])
    assert db.scalar(select(func.count()).select_from(NewsItem)) == 4
    assert db.scalar(select(func.count()).select_from(Event)) == 3
