"""Build events from SEC 8-K filings and linked news, with novelty and story clustering.

Novelty: a news event is a "repeat" when an earlier event of the same company within
NOVELTY_WINDOW is about the same story: cosine similarity of headline embeddings at least
REPEAT_SIMILARITY (or, without an embedding model, word overlap at least REPEAT_OVERLAP).
Repeats join the first event's cluster, so a story's coverage can be counted and collapsed.
"""

import hashlib
import logging
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Company, Event, Filing, NewsItem, NewsMention, Security
from app.events.linking import mention, search_query
from app.events.taxonomy import CLASSIFIER_VERSION, classify_eight_k, classify_headline
from app.filings.sections import EIGHT_K_ITEMS
from app.providers.base import ProviderError, record_fetch
from app.providers.embeddings import EmbeddingProvider
from app.providers.gdelt import GdeltClient

logger = logging.getLogger(__name__)

NOVELTY_WINDOW = timedelta(hours=72)
REPEAT_SIMILARITY = 0.82
REPEAT_OVERLAP = 0.5
EIGHT_K_FORMS = ("8-K", "8-K/A")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or the to with says said".split()
)


def _words(title: str) -> set[str]:
    return {w for w in _WORD.findall(title.lower()) if w not in _STOP and len(w) > 1}


def overlap(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def tickers_of(db: Session, company: Company) -> list[str]:
    return list(
        db.scalars(
            select(Security.ticker)
            .where(Security.company_id == company.id, Security.is_active)
            .order_by(Security.id)
        )
    )


def _novelty(
    db: Session,
    company_id: int,
    occurred_at: datetime,
    title: str,
    embedding: list[float] | None,
    model: str | None,
) -> tuple[Event | None, float | None]:
    """The most similar earlier news event in the window, if similar enough to be a repeat."""
    earlier = (
        select(Event, NewsItem)
        .join(NewsItem, NewsItem.id == Event.news_id)
        .where(
            Event.company_id == company_id,
            Event.occurred_at >= occurred_at - NOVELTY_WINDOW,
            Event.occurred_at <= occurred_at,
        )
    )
    if embedding is not None and model is not None:
        distance = NewsItem.embedding.cosine_distance(embedding)
        row = db.execute(
            earlier.add_columns(distance.label("d"))
            .where(NewsItem.embedding.is_not(None), NewsItem.embedding_model == model)
            .order_by(distance)
            .limit(1)
        ).first()
        if row is None:
            return None, None
        similarity = 1 - float(row.d)
        return (row.Event if similarity >= REPEAT_SIMILARITY else None), similarity
    best: tuple[float, Event | None] = (0.0, None)
    for event, news in db.execute(earlier).all():
        score = overlap(title, news.title)
        if score > best[0]:
            best = (score, event)
    return (best[1] if best[0] >= REPEAT_OVERLAP else None), (best[0] if best[1] else None)


def _add_event(db: Session, event: Event, cluster_of: Event | None) -> Event:
    db.add(event)
    db.flush()
    event.cluster_id = cluster_of.cluster_id if cluster_of else event.id
    return event


def refresh_company_news(
    db: Session,
    company: Company,
    gdelt: GdeltClient,
    embedder: EmbeddingProvider | None,
    *,
    timespan: str = "7d",
) -> dict[str, Any]:
    """Search GDELT for the company, store new headlines, and create events for them."""
    tickers = tickers_of(db, company)
    if not tickers:
        return {"status": "no_listing"}
    query = search_query(company.legal_name, tickers[0])
    try:
        result = gdelt.search(query, timespan=timespan)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        return {"status": "error", "message": error.message}
    fetch = record_fetch(
        db,
        result.meta,
        status="success",
        record_count=len(result.data),
        rejected_count=result.rejected_count,
    )

    created: list[NewsItem] = []
    for article in sorted(result.data, key=lambda a: a.seen_at):
        url_hash = hashlib.sha256(article.url.encode()).hexdigest()
        news_id = db.scalar(
            insert(NewsItem)
            .values(
                provider="gdelt",
                url=article.url,
                url_sha256=url_hash,
                title=article.title,
                domain=article.domain,
                language=article.language,
                source_country=article.source_country,
                seen_at=article.seen_at,
                fetch_id=fetch.id,
            )
            .on_conflict_do_nothing()
            .returning(NewsItem.id)
        )
        item = (
            db.get(NewsItem, news_id)
            if news_id
            else db.scalar(
                select(NewsItem).where(
                    NewsItem.provider == "gdelt", NewsItem.url_sha256 == url_hash
                )
            )
        )
        assert item is not None
        if news_id:
            created.append(item)
        link = mention(item.title, company.legal_name, tickers)
        db.execute(
            insert(NewsMention)
            .values(
                news_id=item.id,
                company_id=company.id,
                method=link.method,
                confidence=link.confidence,
            )
            .on_conflict_do_nothing()
        )

    if embedder is not None and created:
        try:
            vectors = embedder.embed_documents([item.title for item in created])
            for item, vector in zip(created, vectors, strict=True):
                item.embedding = vector
                item.embedding_model = embedder.name
        except Exception:
            logger.exception("news_embedding_failed", extra={"items": len(created)})
    db.flush()
    events = build_news_events(db, company)
    db.commit()
    reclassify_stale(db, company)
    return {
        "status": "current",
        "articles": len(result.data),
        "new": len(created),
        "events": events,
    }


def build_news_events(db: Session, company: Company) -> int:
    """Events for high-confidence news mentions that have none yet, oldest first."""
    pending = db.execute(
        select(NewsItem, NewsMention)
        .join(NewsMention, NewsMention.news_id == NewsItem.id)
        .outerjoin(Event, and_(Event.news_id == NewsItem.id, Event.company_id == company.id))
        .where(
            NewsMention.company_id == company.id,
            NewsMention.confidence == "high",
            Event.id.is_(None),
        )
        .order_by(NewsItem.seen_at, NewsItem.id)
    ).all()
    for item, link in pending:
        label = classify_headline(item.title)
        prior, similarity = _novelty(
            db, company.id, item.seen_at, item.title, item.embedding, item.embedding_model
        )
        _add_event(
            db,
            Event(
                company_id=company.id,
                event_type=label.event_type,
                title=item.title,
                occurred_at=item.seen_at,
                source_kind="news",
                news_id=item.id,
                novelty="repeat" if prior else "first",
                prior_similarity=similarity,
                evidence={
                    "source": "news",
                    "outlet": item.domain,
                    "link_method": link.method,
                    "rule": label.rule,
                    "matched": label.matched,
                    "similarity_method": "embedding" if item.embedding is not None else "words",
                },
                classifier_version=CLASSIFIER_VERSION,
            ),
            prior,
        )
    return len(pending)


def build_filing_events(db: Session, company: Company) -> int:
    """One event per 8-K without one. Filings are primary sources, so always "first"."""
    filings = db.scalars(
        select(Filing)
        .outerjoin(Event, and_(Event.filing_id == Filing.id, Event.company_id == company.id))
        .where(
            Filing.company_id == company.id,
            Filing.form.in_(EIGHT_K_FORMS),
            Event.id.is_(None),
        )
        .order_by(Filing.filed_date, Filing.accession)
    ).all()
    for filing in filings:
        items = [i.strip() for i in (filing.items or "").split(",") if i.strip()]
        label = classify_eight_k(items)
        described = [f"{i} {EIGHT_K_ITEMS[i]}" for i in items if i in EIGHT_K_ITEMS and i != "9.01"]
        title = f"{filing.form}: " + (
            "; ".join(described) or (filing.description or "Current report")
        )
        occurred = filing.accepted_at or datetime.combine(filing.filed_date, time(), tzinfo=UTC)
        _add_event(
            db,
            Event(
                company_id=company.id,
                event_type=label.event_type,
                title=title[:500],
                occurred_at=occurred,
                source_kind="filing",
                filing_id=filing.id,
                novelty="first",
                prior_similarity=None,
                evidence={
                    "source": "SEC 8-K",
                    "items": items,
                    "accession": filing.accession,
                    "rule": label.rule,
                    "amended": filing.form.endswith("/A"),
                },
                classifier_version=CLASSIFIER_VERSION,
            ),
            None,
        )
    db.commit()
    return len(filings)


def reclassify_stale(db: Session, company: Company) -> int:
    """Relabel events classified by an older rule set; novelty and clusters are kept."""
    stale = db.scalars(
        select(Event).where(
            Event.company_id == company.id, Event.classifier_version != CLASSIFIER_VERSION
        )
    ).all()
    for event in stale:
        if event.source_kind == "filing":
            label = classify_eight_k(list(event.evidence.get("items") or []))
        else:
            label = classify_headline(event.title)
        event.event_type = label.event_type
        event.evidence = {**event.evidence, "rule": label.rule, "matched": label.matched}
        event.classifier_version = CLASSIFIER_VERSION
    db.commit()
    return len(stale)
