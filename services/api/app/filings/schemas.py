from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.providers.schemas import SourceRef

IndexStatus = Literal["current", "stale", "unavailable", "not_configured", "not_applicable"]
DocumentStatus = Literal["not_loaded", "loaded", "failed", "unsupported", "not_issuer"]


class FilingOut(BaseModel):
    accession: str
    form: str
    filed_date: date
    accepted_at: datetime | None
    report_date: date | None
    description: str | None
    # 8-K item numbers with their official titles, e.g. "2.02 Results of Operations…".
    items: list[str]
    size: int | None
    document_status: DocumentStatus
    document_error: str | None
    # Filing index page on sec.gov, and the primary document itself.
    sec_url: str
    document_url: str | None


class FormCount(BaseModel):
    form: str
    count: int


class FilingsResponse(BaseModel):
    ticker: str
    cik: int | None
    status: IndexStatus
    message: str | None
    total: int
    forms: list[FormCount]
    filings: list[FilingOut]
    history_loaded: bool
    documents_loaded: int
    # Stored passages; those worth embedding (not financial statements or exhibits); and
    # those already embedded by the active model.
    passages: int
    passages_embeddable: int
    passages_embedded: int
    sources: list[SourceRef]


class SectionOut(BaseModel):
    key: str
    title: str
    part: str | None
    item: str | None
    char_count: int
    text: str


class FilingDetail(BaseModel):
    ticker: str
    filing: FilingOut
    sections: list[SectionOut]
    previous: FilingOut | None
    extractor_version: str | None
    sources: list[SourceRef]


class WordOpOut(BaseModel):
    op: Literal["equal", "insert", "delete"]
    text: str


class DiffBlockOut(BaseModel):
    kind: Literal["same", "added", "removed", "changed"]
    before: str | None
    after: str | None
    words: list[WordOpOut]


class DiffSummaryOut(BaseModel):
    same: int
    added: int
    removed: int
    changed: int
    words_added: int
    words_removed: int


class SectionDiffOut(BaseModel):
    ticker: str
    section_key: str
    title: str
    current: FilingOut
    previous: FilingOut
    # False when the previous filing has no section with this key.
    comparable: bool
    summary: DiffSummaryOut | None
    blocks: list[DiffBlockOut]


class IndexDocumentsIn(BaseModel):
    forms: list[str] = Field(default_factory=lambda: ["10-K", "10-Q", "8-K"], max_length=10)
    limit: int = Field(default=8, ge=1, le=25)
    # Passages to embed in this request (~0.1 s each on a CPU); run again for the rest.
    embed_limit: int = Field(default=200, ge=0, le=2000)


class IndexDocumentsOut(BaseModel):
    ticker: str
    outcome: dict[str, int]
    embedded: int
    embedding_remaining: int
    embedding_model: str | None


class HistoryOut(BaseModel):
    ticker: str
    added: int


class SearchHitOut(BaseModel):
    ticker: str | None
    company_name: str
    filing: FilingOut
    section_key: str
    section_title: str
    # Offsets of the passage within the section text (the citation).
    char_start: int
    char_end: int
    # Passage excerpt; U+E000 / U+E001 delimit matched terms.
    snippet: str
    score: float
    text_rank: int | None
    vector_rank: int | None
    vector_distance: float | None


class SearchResponse(BaseModel):
    query: str
    mode: Literal["hybrid", "text"]
    embedding_model: str | None
    hits: list[SearchHitOut]


class InsiderTransactionOut(BaseModel):
    accession: str
    form: str
    filed_date: date
    # Filing index page on sec.gov.
    sec_url: str
    owner_name: str
    owner_cik: int | None
    joint_owners: int
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str | None
    is_derivative: bool
    security_title: str | None
    transaction_date: date | None
    transaction_code: str | None
    shares: float | None
    price: float | None
    # shares × price when both are reported.
    value: float | None
    acquired_disposed: str | None
    shares_owned_after: float | None
    ownership: str | None
    ownership_nature: str | None
    rule_10b5_1: bool | None
    underlying_security: str | None
    exercise_price: float | None


class TradeTotals(BaseModel):
    transactions: int
    shares: float
    # Sum of shares × price over transactions that report a price.
    value: float
    insiders: int


class InsiderSummary(BaseModel):
    months: int
    since: date
    # Open-market trades only: code P (purchase) and S (sale), non-derivative.
    purchases: TradeTotals
    sales: TradeTotals


class InsidersResponse(BaseModel):
    ticker: str
    status: IndexStatus
    message: str | None
    filings_loaded: int
    filings_pending: int
    summary: InsiderSummary
    transactions: list[InsiderTransactionOut]
