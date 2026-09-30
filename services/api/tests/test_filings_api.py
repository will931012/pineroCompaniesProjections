import hashlib
import math
from collections.abc import Callable, Sequence

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.routes import get_sec_client
from app.db.models import Filing, FilingChunk, FilingSection, InsiderTransaction, ProviderFetch
from app.db.models.filings import EMBEDDING_DIMENSIONS
from app.filings.routes import get_embedder
from app.filings.search import MARK_START
from tests.conftest import LoggedIn
from tests.fakes import sec_client_with, submissions_payload
from tests.fixtures_filings import CIK, DOCUMENTS, PAGES, RISK_D, filings_block

pytestmark = pytest.mark.integration

ROWS = [[CIK, "Example Devices Inc.", "EXDV", "Nasdaq"]]
TEN_K_2024 = "0000999001-24-000040"
TEN_K_2023 = "0000999001-23-000010"


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words point the same way."""

    name = "fake-bag-of-words"
    dimensions = EMBEDDING_DIMENSIONS

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            stem = word.strip(".,;:()\"'")[:6]
            if len(stem) > 3:
                digest = hashlib.sha256(stem.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def sec(override, seed_directory) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    calls: list[httpx.Request] = []
    client = sec_client_with(
        submissions={CIK: submissions_payload(CIK, filings=filings_block())},
        documents=DOCUMENTS,
        pages=PAGES,
        calls=calls,
    )
    override(get_sec_client, lambda: client)
    return calls


@pytest.fixture
def viewer(make_user: Callable[..., str], login: Callable[[str], LoggedIn]) -> LoggedIn:
    return login(make_user("viewer@example.com", "viewer"))


def test_filing_index_lists_filings_with_provenance(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    body = analyst.client.get("/api/v1/companies/EXDV/filings").json()

    assert body["status"] == "current" and body["total"] == 6
    assert {(f["form"], f["count"]) for f in body["forms"]} == {
        ("4", 2),
        ("10-K", 2),
        ("10-Q", 1),
        ("8-K", 1),
    }
    eight_k = next(f for f in body["filings"] if f["form"] == "8-K")
    assert eight_k["items"] == [
        "2.02 Results of Operations and Financial Condition",
        "9.01 Financial Statements and Exhibits",
    ]
    assert eight_k["sec_url"] == (
        "https://www.sec.gov/Archives/edgar/data/999001/000099900124000035/"
        "0000999001-24-000035-index.htm"
    )
    assert eight_k["document_status"] == "not_loaded"
    assert body["sources"][0]["dataset"] == "submissions"

    # Within the refresh interval, a second request reads the stored index.
    requests_before = len(sec)
    only_10k = analyst.client.get("/api/v1/companies/EXDV/filings", params={"forms": "10-k"}).json()
    assert len(sec) == requests_before
    assert [f["accession"] for f in only_10k["filings"]] == [TEN_K_2024, TEN_K_2023]


def test_opening_a_filing_extracts_sections_and_passages(
    analyst: LoggedIn,
    sec,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    body = analyst.client.get(f"/api/v1/companies/EXDV/filings/{TEN_K_2024}").json()

    assert body["filing"]["document_status"] == "loaded"
    assert [s["key"] for s in body["sections"]] == ["item_1", "item_1a", "item_7", "item_8"]
    risk = next(s for s in body["sections"] if s["key"] == "item_1a")
    assert RISK_D in risk["text"]
    assert body["previous"]["accession"] == TEN_K_2023
    assert body["sources"][0]["dataset"] == "filing_document"

    section_text = db.scalar(select(FilingSection.text).where(FilingSection.key == "item_1a"))
    chunks = db.scalars(
        select(FilingChunk).join(FilingSection).where(FilingSection.key == "item_1a")
    )
    for chunk in chunks:
        assert section_text[chunk.char_start : chunk.char_end] == chunk.text  # type: ignore[index]
        assert chunk.embedding is None  # no embedding provider configured in tests


def test_section_diff_against_previous_annual_report(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    body = analyst.client.get(
        f"/api/v1/companies/EXDV/filings/{TEN_K_2024}/diff", params={"section": "item_1a"}
    ).json()

    assert body["comparable"] is True
    assert body["previous"]["accession"] == TEN_K_2023
    assert body["previous"]["document_status"] == "loaded"  # loaded on demand
    assert [b["kind"] for b in body["blocks"]] == ["same", "changed", "removed", "added"]
    assert body["summary"]["changed"] == 1 and body["summary"]["added"] == 1

    missing = analyst.client.get(
        f"/api/v1/companies/EXDV/filings/{TEN_K_2023}/diff", params={"section": "item_1a"}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "no_previous_filing"


def test_insider_transactions_exclude_filings_about_other_issuers(
    analyst: LoggedIn,
    sec,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    body = analyst.client.get("/api/v1/companies/EXDV/insiders", params={"months": 120}).json()

    assert body["filings_loaded"] == 1 and body["filings_pending"] == 0
    assert (
        db.scalar(select(Filing.document_status).where(Filing.accession == "0000999001-24-000049"))
        == "not_issuer"
    )
    rows = body["transactions"]
    assert [(r["transaction_code"], r["is_derivative"]) for r in rows] == [
        ("P", False),
        ("S", False),
        ("M", True),
    ]
    sale = rows[1]
    assert (sale["owner_name"], sale["officer_title"], sale["value"]) == (
        "Doe Jane",
        "Chief Financial Officer",
        150250.0,
    )
    assert sale["rule_10b5_1"] is True
    assert rows[2]["price"] is None and rows[2]["value"] is None
    summary = body["summary"]
    assert (summary["purchases"]["transactions"], summary["purchases"]["value"]) == (1, 28000.0)
    assert (summary["sales"]["shares"], summary["sales"]["insiders"]) == (1000.0, 1)
    assert db.scalar(select(func.count()).select_from(InsiderTransaction)) == 3


def test_full_text_search_cites_the_exact_passage(analyst: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    indexed = analyst.client.post(
        "/api/v1/companies/EXDV/filings/index", json={"limit": 10}, headers=analyst.headers()
    ).json()
    assert indexed["outcome"] == {"loaded": 4}
    # No embedding provider in this test: nothing is embedded, full-text search still works.
    assert indexed["embedded"] == 0 and indexed["embedding_model"] is None
    assert indexed["embedding_remaining"] > 0

    body = analyst.client.get(
        "/api/v1/search/filings", params={"q": "export controls semiconductors", "tickers": "EXDV"}
    ).json()

    assert body["mode"] == "text" and body["embedding_model"] is None
    hit = body["hits"][0]
    assert (hit["ticker"], hit["filing"]["accession"], hit["section_key"]) == (
        "EXDV",
        TEN_K_2024,
        "item_1a",
    )
    assert MARK_START in hit["snippet"] and hit["vector_rank"] is None
    detail = analyst.client.get(f"/api/v1/companies/EXDV/filings/{TEN_K_2024}").json()
    section = next(s for s in detail["sections"] if s["key"] == hit["section_key"])
    assert RISK_D in section["text"][hit["char_start"] : hit["char_end"]]

    # "relationship" appears nowhere; passages matching the other words still come back.
    partial = analyst.client.get(
        "/api/v1/search/filings", params={"q": "export controls relationship", "tickers": "EXDV"}
    ).json()
    assert partial["hits"] and partial["hits"][0]["section_key"] == "item_1a"
    assert MARK_START in partial["hits"][0]["snippet"]

    filtered = analyst.client.get(
        "/api/v1/search/filings", params={"q": "export controls", "forms": "10-Q"}
    ).json()
    assert filtered["hits"] == []


def test_hybrid_search_uses_embeddings_from_the_active_model(
    analyst: LoggedIn,
    sec,  # type: ignore[no-untyped-def]
    override,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    override(get_embedder, lambda: FakeEmbedder())
    indexed = analyst.client.post(
        "/api/v1/companies/EXDV/filings/index",
        json={"forms": ["10-K"], "limit": 5},
        headers=analyst.headers(),
    ).json()
    assert indexed["embedding_remaining"] == 0 and indexed["embedding_model"] == FakeEmbedder.name
    listing = analyst.client.get("/api/v1/companies/EXDV/filings").json()
    assert listing["passages_embedded"] == listing["passages_embeddable"] > 0
    # Financial statements (Item 8) stay in full-text search only.
    assert listing["passages_embeddable"] < listing["passages"]
    models = set(db.scalars(select(FilingChunk.embedding_model).distinct()))
    assert models == {FakeEmbedder.name, None}

    # No word of the query appears verbatim, so only the vector ranking can find it.
    body = analyst.client.get(
        "/api/v1/search/filings", params={"q": "semiconductor exports restricted customers"}
    ).json()
    assert body["mode"] == "hybrid" and body["embedding_model"] == FakeEmbedder.name
    top = body["hits"][0]
    assert top["section_key"] == "item_1a" and top["vector_rank"] == 1


def test_viewers_read_filings_but_cannot_bulk_index(viewer: LoggedIn, sec) -> None:  # type: ignore[no-untyped-def]
    assert viewer.client.get("/api/v1/companies/EXDV/filings").status_code == 200
    response = viewer.client.post(
        "/api/v1/companies/EXDV/filings/index", json={}, headers=viewer.headers()
    )
    assert response.status_code == 403


def test_older_filing_history_and_invalid_accessions(
    analyst: LoggedIn,
    sec,  # type: ignore[no-untyped-def]
    db: Session,
) -> None:
    analyst.client.get("/api/v1/companies/EXDV/filings")
    added = analyst.client.post(
        "/api/v1/companies/EXDV/filings/history", headers=analyst.headers()
    ).json()
    assert added["added"] == 1
    listing = analyst.client.get("/api/v1/companies/EXDV/filings", params={"forms": "10-K"}).json()
    assert listing["history_loaded"] is True and listing["total"] == 3
    assert "submissions_page" in set(db.scalars(select(ProviderFetch.dataset)))

    bad = analyst.client.get("/api/v1/companies/EXDV/filings/../../etc")
    assert bad.status_code in {404, 422}
    invalid = analyst.client.get("/api/v1/companies/EXDV/filings/not-an-accession")
    assert invalid.json()["error"]["code"] == "invalid_accession"
    unknown = analyst.client.get("/api/v1/companies/EXDV/filings/0000999001-99-999999")
    assert unknown.json()["error"]["code"] == "filing_not_found"
