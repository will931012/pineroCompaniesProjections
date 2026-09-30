from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import AnalystUser, CurrentUser, DbSession
from app.companies.service import resolve_security
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.models import (
    Company,
    Event,
    Filing,
    FilingSection,
    NewsItem,
    NewsMention,
    ProviderFetch,
    Security,
    Watchlist,
    WatchlistItem,
)
from app.events.schemas import (
    EarningsRelease,
    EarningsResponse,
    EventBrief,
    EventsResponse,
    EventTypeOut,
    FeedItem,
    FeedResponse,
    FilingRef,
    NewsItemOut,
    NewsRef,
    NewsRefreshOut,
    NewsResponse,
    TimelineEvent,
)
from app.events.service import build_filing_events, reclassify_stale, refresh_company_news
from app.events.taxonomy import CLASSIFIER_VERSION, EVENT_TYPES
from app.filings.routes import Embedder, filing_out
from app.providers.gdelt import GdeltClient, build_gdelt_client
from app.providers.schemas import source_refs

router = APIRouter(tags=["events"])

ATTRIBUTION = (
    "News index: The GDELT Project (gdeltproject.org). Articles belong to their publishers."
)


def get_gdelt() -> GdeltClient:
    return build_gdelt_client(get_settings().sec_user_agent)


Gdelt = Annotated[GdeltClient, Depends(get_gdelt)]


def _clusters(db: Session, cluster_ids: set[int]) -> dict[int, tuple[int, list[str]]]:
    """cluster id -> (events in the story, distinct outlets that covered it)."""
    if not cluster_ids:
        return {}
    rows = db.execute(
        select(Event.cluster_id, NewsItem.domain)
        .outerjoin(NewsItem, NewsItem.id == Event.news_id)
        .where(Event.cluster_id.in_(cluster_ids))
    ).all()
    sizes: dict[int, int] = defaultdict(int)
    outlets: dict[int, set[str]] = defaultdict(set)
    for cluster_id, domain in rows:
        if cluster_id is None:
            continue
        sizes[cluster_id] += 1
        if domain:
            outlets[cluster_id].add(domain)
    return {c: (sizes[c], sorted(outlets[c])) for c in sizes}


def _filing_ref(filing: Filing, company: Company, ticker: str) -> FilingRef:
    out = filing_out(filing, company.cik)
    return FilingRef(
        accession=filing.accession,
        form=filing.form,
        filed_date=filing.filed_date,
        items=out.items,
        app_path=f"/companies/{ticker}/filings/{filing.accession}",
        sec_url=out.sec_url,
    )


def _timeline(
    db: Session, rows: list[tuple[Event, Filing | None, NewsItem | None]], company: Company,
    ticker: str,
) -> list[TimelineEvent]:  # fmt: skip
    clusters = _clusters(db, {e.cluster_id for e, _f, _n in rows if e.cluster_id is not None})
    return [
        TimelineEvent(
            id=event.id,
            event_type=event.event_type,
            label=EVENT_TYPES.get(event.event_type, event.event_type),
            title=event.title,
            occurred_at=event.occurred_at,
            source_kind=event.source_kind,  # type: ignore[arg-type]
            novelty=event.novelty,  # type: ignore[arg-type]
            cluster_id=event.cluster_id,
            cluster_size=clusters.get(event.cluster_id or -1, (1, []))[0],
            outlets=clusters.get(event.cluster_id or -1, (1, []))[1],
            filing=_filing_ref(filing, company, ticker) if filing else None,
            news=NewsRef(url=news.url, domain=news.domain) if news else None,
            evidence=event.evidence,
        )
        for event, filing, news in rows
    ]


def _event_rows(
    db: Session,
    company_ids: list[int],
    since: datetime,
    extra: ColumnElement[bool] | None = None,
) -> list[tuple[Event, Filing | None, NewsItem | None]]:
    query = (
        select(Event, Filing, NewsItem)
        .outerjoin(Filing, Filing.id == Event.filing_id)
        .outerjoin(NewsItem, NewsItem.id == Event.news_id)
        .where(Event.company_id.in_(company_ids), Event.occurred_at >= since)
    )
    if extra is not None:
        query = query.where(extra)
    ordered = query.order_by(Event.occurred_at.desc(), Event.id.desc())
    return [(r[0], r[1], r[2]) for r in db.execute(ordered)]


@router.get("/events/types", response_model=list[EventTypeOut])
def event_types(_: CurrentUser) -> list[EventTypeOut]:
    return [EventTypeOut(key=k, label=v) for k, v in EVENT_TYPES.items()]


@router.get("/companies/{ticker}/news", response_model=NewsResponse)
def company_news(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 14,
    confidence: Literal["high", "all"] = "high",
    limit: Annotated[int, Query(ge=1, le=300)] = 150,
) -> NewsResponse:
    security = resolve_security(db, ticker)
    company = security.company
    since = utcnow() - timedelta(days=days)
    query = (
        select(NewsItem, NewsMention, Event)
        .join(NewsMention, NewsMention.news_id == NewsItem.id)
        .outerjoin(Event, (Event.news_id == NewsItem.id) & (Event.company_id == company.id))
        .where(NewsMention.company_id == company.id, NewsItem.seen_at >= since)
    )
    hidden = 0
    if confidence == "high":
        hidden = (
            db.scalar(
                select(func.count())
                .select_from(NewsMention)
                .join(NewsItem, NewsItem.id == NewsMention.news_id)
                .where(
                    NewsMention.company_id == company.id,
                    NewsMention.confidence == "low",
                    NewsItem.seen_at >= since,
                )
            )
            or 0
        )
        query = query.where(NewsMention.confidence == "high")
    rows = db.execute(
        query.order_by(NewsItem.seen_at.desc(), NewsItem.id.desc()).limit(limit)
    ).all()
    clusters = _clusters(db, {e.cluster_id for _n, _m, e in rows if e and e.cluster_id})
    last_fetch = db.scalar(
        select(ProviderFetch)
        .join(NewsItem, NewsItem.fetch_id == ProviderFetch.id)
        .join(NewsMention, NewsMention.news_id == NewsItem.id)
        .where(NewsMention.company_id == company.id)
        .order_by(ProviderFetch.retrieved_at.desc())
        .limit(1)
    )
    return NewsResponse(
        ticker=security.ticker,
        days=days,
        items=[
            NewsItemOut(
                id=news.id,
                title=news.title,
                url=news.url,
                domain=news.domain,
                source_country=news.source_country,
                seen_at=news.seen_at,
                link_method=link.method,  # type: ignore[arg-type]
                confidence=link.confidence,  # type: ignore[arg-type]
                event=EventBrief(
                    id=event.id,
                    event_type=event.event_type,
                    label=EVENT_TYPES.get(event.event_type, event.event_type),
                    novelty=event.novelty,  # type: ignore[arg-type]
                    cluster_id=event.cluster_id,
                    cluster_size=clusters.get(event.cluster_id or -1, (1, []))[0],
                    prior_similarity=event.prior_similarity,
                )
                if event
                else None,
            )
            for news, link, event in rows
        ],
        low_confidence_hidden=hidden,
        last_refreshed=last_fetch.retrieved_at if last_fetch else None,
        attribution=ATTRIBUTION,
        sources=source_refs([last_fetch]),
    )


@router.post("/companies/{ticker}/news/refresh", response_model=NewsRefreshOut)
def refresh_news(
    ticker: str, _: AnalystUser, db: DbSession, embedder: Embedder, gdelt: Gdelt
) -> NewsRefreshOut:
    """Search the news index now (one GDELT request; GDELT allows one every 5 seconds)."""
    security = resolve_security(db, ticker)
    outcome = refresh_company_news(db, security.company, gdelt, embedder)
    return NewsRefreshOut(ticker=security.ticker, **outcome)


@router.get("/companies/{ticker}/events", response_model=EventsResponse)
def company_events(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
    include_repeats: bool = False,
) -> EventsResponse:
    security = resolve_security(db, ticker)
    company = security.company
    build_filing_events(db, company)  # 8-Ks indexed since the last visit
    reclassify_stale(db, company)
    extra = None if include_repeats else Event.novelty == "first"
    rows = _event_rows(db, [company.id], utcnow() - timedelta(days=days), extra)
    events = _timeline(db, rows, company, security.ticker)
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event.event_type] += 1
    return EventsResponse(
        ticker=security.ticker,
        days=days,
        events=events,
        counts=dict(counts),
        classifier_version=CLASSIFIER_VERSION,
    )


@router.get("/companies/{ticker}/earnings", response_model=EarningsResponse)
def company_earnings(ticker: str, _: CurrentUser, db: DbSession) -> EarningsResponse:
    security = resolve_security(db, ticker)
    company = security.company
    build_filing_events(db, company)
    reclassify_stale(db, company)
    rows = db.execute(
        select(Event, Filing)
        .join(Filing, Filing.id == Event.filing_id)
        .where(Event.company_id == company.id, Event.event_type == "earnings")
        .order_by(Event.occurred_at.desc())
        .limit(40)
    ).all()
    releases = []
    for event, filing in rows:
        exhibit = db.scalar(
            select(FilingSection).where(
                FilingSection.filing_id == filing.id, FilingSection.key == "exhibit_99"
            )
        )
        releases.append(
            EarningsRelease(
                event_id=event.id,
                occurred_at=event.occurred_at,
                filing=_filing_ref(filing, company, security.ticker),
                press_release_excerpt=exhibit.text[:700] if exhibit else None,
                press_release_chars=exhibit.char_count if exhibit else None,
                document_status=filing.document_status,
            )
        )
    return EarningsResponse(
        ticker=security.ticker,
        releases=releases,
        note=(
            "Earnings releases are 8-K filings with Item 2.02 (Results of Operations). The press "
            "release is the filing's Exhibit 99. Upcoming report dates need an earnings-calendar "
            "source, which is not configured."
        ),
    )


@router.get("/feed", response_model=FeedResponse)
def feed(
    user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> FeedResponse:
    """First reports of events for companies on the user's watchlists."""
    company_ids = list(
        db.scalars(
            select(Security.company_id)
            .join(WatchlistItem, WatchlistItem.security_id == Security.id)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
            .where(Watchlist.owner_id == user.id)
            .distinct()
        )
    )
    if not company_ids:
        return FeedResponse(items=[], companies=0)
    for company in db.scalars(select(Company).where(Company.id.in_(company_ids))):
        reclassify_stale(db, company)
    rows = _event_rows(db, company_ids, utcnow() - timedelta(days=days), Event.novelty == "first")[
        :limit
    ]
    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(company_ids)))}
    tickers: dict[int, str] = {}
    for company_id, ticker in db.execute(
        select(Security.company_id, Security.ticker)
        .where(Security.company_id.in_(company_ids), Security.is_active)
        .order_by(Security.company_id, Security.id)
    ):
        tickers.setdefault(company_id, ticker)
    items = []
    for row in rows:
        event = row[0]
        company = companies[event.company_id]
        ticker = tickers.get(company.id, "")
        items.append(
            FeedItem(
                ticker=ticker,
                company_name=company.legal_name,
                event=_timeline(db, [row], company, ticker)[0],
            )
        )
    return FeedResponse(items=items, companies=len(company_ids))
