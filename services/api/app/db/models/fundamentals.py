from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

VALUE = Numeric(28, 6)


class FinancialFact(Base):
    """One XBRL fact value as first made public in a periodic filing.

    `filed_date` is when the value became public, which is what point-in-time
    reconstruction filters on. A restated value is a new row with a later
    `filed_date`; rows are never updated in place.
    """

    __tablename__ = "financial_facts"
    __table_args__ = (
        Index(
            "uq_financial_facts_observation",
            "company_id",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            "value",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_financial_facts_company_filed", "company_id", "filed_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    taxonomy: Mapped[str] = mapped_column(String(20))
    concept: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    # Null for instant (balance-sheet) facts.
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(VALUE)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(4))
    form: Mapped[str] = mapped_column(String(12))
    accession: Mapped[str] = mapped_column(String(25))
    filed_date: Mapped[date] = mapped_column(Date)
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )


class CompanyMetric(Base):
    """Latest value of each screenable metric for a company, with its inputs' dates."""

    __tablename__ = "company_metrics"
    __table_args__ = (Index("ix_company_metrics_metric_value", "metric", "value"),)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    # "FY2024", "TTM Q2 FY2025", "2026-09-26" (price-based) …
    basis: Mapped[str] = mapped_column(String(160))
    period_end: Mapped[date | None] = mapped_column(Date)
    # Latest date on which any input became public.
    available_date: Mapped[date | None] = mapped_column(Date)
    derived_from_price: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    formula_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
