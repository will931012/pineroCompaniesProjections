from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.schemas import SourceRef


class EventTypeOut(BaseModel):
    key: str
    label: str


class EventBrief(BaseModel):
    id: int
    event_type: str
    label: str
    novelty: Literal["first", "repeat"]
    cluster_id: int | None
    # Events about the same story (this one included) and the outlets that covered it.
    cluster_size: int
    prior_similarity: float | None


class NewsItemOut(BaseModel):
    id: int
    title: str
    url: str
    domain: str | None
    source_country: str | None
    seen_at: datetime
    link_method: Literal["title_ticker", "title_name", "query_only"]
    confidence: Literal["high", "low"]
    event: EventBrief | None


class NewsResponse(BaseModel):
    ticker: str
    days: int
    items: list[NewsItemOut]
    low_confidence_hidden: int
    last_refreshed: datetime | None
    attribution: str
    sources: list[SourceRef]


class NewsRefreshOut(BaseModel):
    ticker: str
    status: str
    message: str | None = None
    articles: int = 0
    new: int = 0
    events: int = 0


class FilingRef(BaseModel):
    accession: str
    form: str
    filed_date: date
    items: list[str]
    # In-app viewer path and sec.gov index page.
    app_path: str
    sec_url: str


class NewsRef(BaseModel):
    url: str
    domain: str | None


class TimelineEvent(BaseModel):
    id: int
    event_type: str
    label: str
    title: str
    occurred_at: datetime
    source_kind: Literal["filing", "news"]
    novelty: Literal["first", "repeat"]
    cluster_id: int | None
    cluster_size: int
    outlets: list[str]
    filing: FilingRef | None
    news: NewsRef | None
    evidence: dict[str, Any]


class EventsResponse(BaseModel):
    ticker: str
    days: int
    events: list[TimelineEvent]
    counts: dict[str, int]
    classifier_version: str


class EarningsRelease(BaseModel):
    event_id: int
    occurred_at: datetime
    filing: FilingRef
    # Press release (8-K Exhibit 99) text, when the filing's documents are loaded.
    press_release_excerpt: str | None
    press_release_chars: int | None
    document_status: str


class EarningsResponse(BaseModel):
    ticker: str
    releases: list[EarningsRelease]
    note: str


class FeedItem(BaseModel):
    ticker: str
    company_name: str
    event: TimelineEvent


class FeedResponse(BaseModel):
    items: list[FeedItem]
    companies: int
