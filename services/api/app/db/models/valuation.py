"""Phase 5: market rates and saved valuation runs."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class MarketRate(Base):
    """A published rate observation, e.g. the 10-year Treasury par yield on a date."""

    __tablename__ = "market_rates"
    __table_args__ = (UniqueConstraint("series", "tenor", "observed_on"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series: Mapped[str] = mapped_column(String(40))  # treasury_par
    tenor: Mapped[str] = mapped_column(String(20))  # "10 Yr"
    observed_on: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 8))  # decimal, 0.0529 = 5.29%
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )


class ValuationRun(Base):
    """A saved valuation: the exact assumptions (with the source of every default) and outputs."""

    __tablename__ = "valuation_runs"
    __table_args__ = (Index("ix_valuation_runs_company_created", "company_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(10))  # dcf | rim | ddm
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    scenarios: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Where each default came from ("US Treasury 10 Yr par yield 2026-09-30", "TTM Q3 FY2026"…).
    sources: Mapped[dict[str, Any]] = mapped_column(JSONB)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Price used for upside/downside, and its date; None when no price was stored.
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    price_date: Mapped[date | None] = mapped_column(Date)
    formula_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
