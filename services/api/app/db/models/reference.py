from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[int | None] = mapped_column(Integer, unique=True)
    legal_name: Mapped[str] = mapped_column(String(240), index=True)
    country: Mapped[str | None] = mapped_column(String(2))
    # Classification comes from SEC SIC codes; `sector` is the SIC division, not GICS.
    classification_system: Mapped[str | None] = mapped_column(String(20))
    sic_code: Mapped[str | None] = mapped_column(String(4))
    sector: Mapped[str | None] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    filer_category: Mapped[str | None] = mapped_column(String(80))
    state_of_incorporation: Mapped[str | None] = mapped_column(String(80))
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))
    website: Mapped[str | None] = mapped_column(String(300))
    hq_city: Mapped[str | None] = mapped_column(String(120))
    hq_region: Mapped[str | None] = mapped_column(String(120))
    former_names: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    directory_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="SET NULL")
    )
    profile_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="SET NULL")
    )
    profile_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fundamentals_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="SET NULL")
    )
    fundamentals_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filings_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # True once the older submissions pages (beyond SEC's most recent ~1,000) are indexed.
    filings_history_loaded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    securities: Mapped[list["Security"]] = relationship(back_populates="company")


class Security(TimestampMixin, Base):
    """A listed instrument. Rows are never deleted when they leave a provider's listing;
    they are marked inactive so historical research keeps delisted securities."""

    __tablename__ = "securities"
    __table_args__ = (
        Index(
            "uq_securities_active_ticker_exchange",
            "ticker",
            "exchange",
            unique=True,
            postgresql_where=text("is_active"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_securities_ticker", "ticker"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    exchange: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str | None] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="SET NULL")
    )

    company: Mapped[Company] = relationship(back_populates="securities")
