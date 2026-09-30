from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Dimension of the embedding column; the configured model must produce vectors this long.
EMBEDDING_DIMENSIONS = 384


class Filing(Base):
    """One entry in a company's EDGAR filing index; the document itself loads on demand."""

    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint("company_id", "accession"),
        Index("ix_filings_company_filed", "company_id", "filed_date"),
        Index("ix_filings_company_form_filed", "company_id", "form", "filed_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    accession: Mapped[str] = mapped_column(String(20))
    form: Mapped[str] = mapped_column(String(20))
    filed_date: Mapped[date] = mapped_column(Date)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_date: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(300))
    items: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[int | None] = mapped_column(Integer)
    is_xbrl: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_inline_xbrl: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    index_fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )
    # not_loaded | loaded | failed | unsupported
    document_status: Mapped[str] = mapped_column(
        String(20), default="not_loaded", server_default=text("'not_loaded'")
    )
    document_error: Mapped[str | None] = mapped_column(String(300))
    document_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="SET NULL")
    )
    extractor_version: Mapped[str | None] = mapped_column(String(20))
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FilingSection(Base):
    """A part of a filing's primary document (10-K Item 1A, 8-K Item 2.02, …) as plain text."""

    __tablename__ = "filing_sections"
    __table_args__ = (UniqueConstraint("filing_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    filing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("filings.id", ondelete="CASCADE"), index=True
    )
    # Stable across filings of the same form, e.g. "item_1a" or "part2_item_1a".
    key: Mapped[str] = mapped_column(String(40))
    part: Mapped[str | None] = mapped_column(String(8))
    item: Mapped[str | None] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    ordinal: Mapped[int] = mapped_column(Integer)
    # Paragraphs separated by a blank line.
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    text_sha256: Mapped[str] = mapped_column(String(64))


class FilingChunk(Base):
    """A passage of a section: the unit of search and citation."""

    __tablename__ = "filing_chunks"
    __table_args__ = (
        UniqueConstraint("section_id", "ordinal"),
        Index("ix_filing_chunks_search", "search", postgresql_using="gin"),
        Index(
            "ix_filing_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    section_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("filing_sections.id", ondelete="CASCADE"), index=True
    )
    # Denormalised so search can filter without joins.
    filing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("filings.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Offsets into the section text, so a citation points at the exact passage.
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    search: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(80))


class InsiderTransaction(Base):
    """One transaction row of a Form 4 (non-derivative or derivative table)."""

    __tablename__ = "insider_transactions"
    __table_args__ = (
        UniqueConstraint("filing_id", "is_derivative", "ordinal"),
        Index("ix_insider_transactions_company_date", "company_id", "transaction_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    filing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("filings.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    # All reporting owners of the filing; the first is shown as the insider.
    owners: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    owner_cik: Mapped[int | None] = mapped_column(BigInteger)
    owner_name: Mapped[str] = mapped_column(String(200))
    is_director: Mapped[bool] = mapped_column(Boolean)
    is_officer: Mapped[bool] = mapped_column(Boolean)
    is_ten_percent_owner: Mapped[bool] = mapped_column(Boolean)
    officer_title: Mapped[str | None] = mapped_column(String(200))
    is_derivative: Mapped[bool] = mapped_column(Boolean)
    ordinal: Mapped[int] = mapped_column(Integer)
    security_title: Mapped[str | None] = mapped_column(String(200))
    transaction_date: Mapped[date | None] = mapped_column(Date)
    # SEC transaction code: P purchase, S sale, A grant, M exercise, F tax withholding, G gift …
    transaction_code: Mapped[str | None] = mapped_column(String(4))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    # A acquired, D disposed
    acquired_disposed: Mapped[str | None] = mapped_column(String(1))
    shares_owned_after: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    # D direct, I indirect
    ownership: Mapped[str | None] = mapped_column(String(1))
    ownership_nature: Mapped[str | None] = mapped_column(String(200))
    # Reported under a Rule 10b5-1 trading plan (checkbox added to Form 4 in 2023).
    rule_10b5_1: Mapped[bool | None] = mapped_column(Boolean)
    underlying_security: Mapped[str | None] = mapped_column(String(200))
    exercise_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
