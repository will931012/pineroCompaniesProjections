"""Phase 4: background jobs, news, events, and alerts."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.db.models.filings import EMBEDDING_DIMENSIONS


class Job(Base):
    """A unit of background work. Workers claim jobs with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_at", "status", "run_at"),
        # At most one queued or running job per dedupe key (e.g. one news poll per hour).
        Index(
            "uq_jobs_dedupe_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(12), default="queued")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsItem(Base):
    """An article found through a news index. Only metadata is kept: no article body."""

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("provider", "url_sha256"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(Text)
    url_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(40))
    source_country: Mapped[str | None] = mapped_column(String(80))
    # When the index first saw the article (GDELT "seendate"); not the outlet's own timestamp.
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsMention(Base):
    """Why a news item is linked to a company, and how confident the link is."""

    __tablename__ = "news_mentions"

    news_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # title_ticker | title_name | query_only
    method: Mapped[str] = mapped_column(String(20))
    # high: the headline names the company; low: only the index's full-text match does.
    confidence: Mapped[str] = mapped_column(String(8))


class Event(Base):
    """Something that happened to a company, from a filing or a news item, with evidence."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_company_occurred", "company_id", "occurred_at"),
        Index(
            "uq_events_company_filing",
            "company_id",
            "filing_id",
            unique=True,
            postgresql_where=text("filing_id IS NOT NULL"),
        ),
        Index(
            "uq_events_company_news",
            "company_id",
            "news_id",
            unique=True,
            postgresql_where=text("news_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # filing | news
    source_kind: Mapped[str] = mapped_column(String(12))
    filing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("filings.id", ondelete="CASCADE")
    )
    news_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("news_items.id", ondelete="CASCADE")
    )
    # Events about the same story share the id of the first event in the story.
    cluster_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # first | repeat
    novelty: Mapped[str] = mapped_column(String(8))
    # Highest similarity to an earlier event of the same company in the lookback window.
    prior_similarity: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    classifier_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # filing | earnings | news | insider | price
    kind: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Companies the rule watches: explicit tickers and/or one of the owner's watchlists.
    tickers: Mapped[list[str]] = mapped_column(JSONB, default=list)
    watchlist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("watchlists.id", ondelete="SET NULL")
    )
    email: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    """One triggered alert. The unique subject key makes evaluation idempotent."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("rule_id", "subject_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    subject_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    # Path inside the app (e.g. /companies/AAPL/filings/…) or an external source URL.
    link: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # pending | sent | failed | not_configured | disabled
    email_status: Mapped[str] = mapped_column(String(16))
    email_error: Mapped[str | None] = mapped_column(String(300))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
