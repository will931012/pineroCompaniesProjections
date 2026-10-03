"""Phase 6: macro data, the point-in-time universe, features, models, predictions, and 13F."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class MacroSeries(Base):
    """A FRED series we track, with the publication lag used when vintages are unavailable."""

    __tablename__ = "macro_series"

    series_id: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. "DGS10"
    title: Mapped[str] = mapped_column(String(300))
    units: Mapped[str] = mapped_column(String(120))
    frequency: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(200))
    release_lag_days: Mapped[int] = mapped_column(Integer)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT")
    )


class MacroObservation(Base):
    """One value of a series as published during [realtime_start, realtime_end] (ALFRED vintages).

    `available_on` is when the value could first be known: the vintage start, or, where FRED's
    vintage history begins after the observation, its period date plus the series' release lag.
    """

    __tablename__ = "macro_observations"
    __table_args__ = (
        UniqueConstraint("series_id", "observation_date", "realtime_start"),
        Index("ix_macro_observations_series_available", "series_id", "available_on"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("macro_series.series_id", ondelete="CASCADE"))
    observation_date: Mapped[date] = mapped_column(Date)
    realtime_start: Mapped[date] = mapped_column(Date)
    realtime_end: Mapped[date] = mapped_column(Date)
    available_on: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )


class UniverseCandidate(Base):
    """A company considered for the model universe, from SEC frames (any year's top revenues)."""

    __tablename__ = "universe_candidates"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    best_rank: Mapped[int] = mapped_column(Integer)  # best calendar-year revenue rank seen
    best_year: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UniverseMember(Base):
    """Top companies by revenue as known on `as_of` (point in time).

    Rows with `has_prices` false were large enough but had no price history (typically
    delisted or renamed tickers); they are kept to measure survivorship bias.
    """

    __tablename__ = "universe_members"
    __table_args__ = (Index("ix_universe_members_company", "company_id", "as_of"),)

    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    revenue_ttm: Mapped[Decimal] = mapped_column(Numeric(24, 2))
    revenue_basis: Mapped[str] = mapped_column(String(40))
    security_id: Mapped[int | None] = mapped_column(
        ForeignKey("securities.id", ondelete="SET NULL")
    )
    has_prices: Mapped[bool] = mapped_column(Boolean)


class PriceCoverage(Base):
    """What the bulk price loader has fetched for a security, so it can resume and back off."""

    __tablename__ = "price_coverage"

    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    loaded_from: Mapped[date | None] = mapped_column(Date)
    loaded_through: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))  # loaded | not_found | error
    error: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeatureSnapshot(Base):
    """Immutable features of one company as of one date, built only from data available then."""

    __tablename__ = "feature_snapshots"
    __table_args__ = (Index("ix_feature_snapshots_company", "company_id", "as_of"),)

    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Latest publication date among the inputs: never after as_of (asserted by leakage tests).
    data_available_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactorScore(Base):
    """Cross-sectional factor score (z-score average) and percentile within the universe."""

    __tablename__ = "factor_scores"
    __table_args__ = (Index("ix_factor_scores_company", "company_id", "as_of"),)

    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    factor: Mapped[str] = mapped_column(String(30), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    percentile: Mapped[float] = mapped_column(Float)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)


class MarketRegime(Base):
    """Rule-based market regime on a date, with the components that produced it."""

    __tablename__ = "market_regimes"

    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    label: Mapped[str] = mapped_column(String(40))
    components: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ModelVersion(Base):
    """A trained model with its evaluation, calibration, and serialised artifact."""

    __tablename__ = "model_versions"
    __table_args__ = (Index("ix_model_versions_name_created", "name", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(60))  # e.g. "direction_21d"
    target: Mapped[str] = mapped_column(String(60))
    horizon_days: Mapped[int] = mapped_column(Integer)
    feature_set_version: Mapped[str] = mapped_column(String(20))
    universe_version: Mapped[str] = mapped_column(String(20))
    code_version: Mapped[str] = mapped_column(String(20))
    trained_from: Mapped[date] = mapped_column(Date)
    trained_through: Mapped[date] = mapped_column(Date)  # last as_of whose label was known
    feature_names: Mapped[list[str]] = mapped_column(JSONB)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Walk-forward out-of-sample evaluation (model and baseline), by fold, regime, and sector.
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    calibration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    importance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    artifact: Mapped[str] = mapped_column(Text)  # LightGBM model text
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | retired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Prediction(Base):
    """A journaled model output. Never edited; outcomes are recorded separately once known."""

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("model_version_id", "company_id", "as_of"),
        Index("ix_predictions_company_as_of", "company_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    probability_raw: Mapped[float] = mapped_column(Float)
    probability: Mapped[float] = mapped_column(Float)  # calibrated P(excess return > 0)
    expected_excess: Mapped[float | None] = mapped_column(Float)  # median forecast
    excess_low: Mapped[float | None] = mapped_column(Float)  # 10th percentile
    excess_high: Mapped[float | None] = mapped_column(Float)  # 90th percentile
    drivers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # top SHAP contributions
    data_available_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PredictionOutcome(Base):
    """What happened after a prediction's horizon elapsed."""

    __tablename__ = "prediction_outcomes"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    end_date: Mapped[date] = mapped_column(Date)
    excess_return: Mapped[float] = mapped_column(Float)
    went_up: Mapped[bool] = mapped_column(Boolean)
    brier: Mapped[float] = mapped_column(Float)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CryptoPrice(Base):
    """Daily crypto bar from Tiingo's crypto endpoint (UTC days; markets trade every day)."""

    __tablename__ = "crypto_prices"

    pair: Mapped[str] = mapped_column(String(20), primary_key=True)  # "btcusd"
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    volume: Mapped[Decimal] = mapped_column(Numeric(28, 8))  # in the base currency
    volume_usd: Mapped[Decimal | None] = mapped_column(Numeric(28, 2))
    trades: Mapped[int | None] = mapped_column(BigInteger)
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT"), index=True
    )


class CusipMapping(Base):
    """CUSIP → listing, from OpenFIGI. `company_id` is set when the ticker is in our directory."""

    __tablename__ = "cusip_mappings"

    cusip: Mapped[str] = mapped_column(String(9), primary_key=True)
    status: Mapped[str] = mapped_column(String(12))  # mapped | no_match
    figi: Mapped[str | None] = mapped_column(String(12))
    composite_figi: Mapped[str | None] = mapped_column(String(12))
    ticker: Mapped[str | None] = mapped_column(String(20))
    exch_code: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(200))
    security_type: Mapped[str | None] = mapped_column(String(60))
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT")
    )
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OwnershipSummary(Base):
    """All 13F holders of a company for one report period (options excluded from totals)."""

    __tablename__ = "ownership_summaries"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    period_of_report: Mapped[date] = mapped_column(Date, primary_key=True)
    holders: Mapped[int] = mapped_column(Integer)
    shares: Mapped[Decimal] = mapped_column(Numeric(24, 0))
    value_usd: Mapped[Decimal] = mapped_column(Numeric(24, 0))
    cusips: Mapped[list[str]] = mapped_column(JSONB)
    dataset: Mapped[str] = mapped_column(String(120))
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT")
    )


class InstitutionalHolding(Base):
    """One manager's reported position (largest holders only), as filed on Form 13F."""

    __tablename__ = "institutional_holdings"
    __table_args__ = (
        Index("ix_institutional_holdings_company_period", "company_id", "period_of_report"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    period_of_report: Mapped[date] = mapped_column(Date)
    filer_cik: Mapped[int] = mapped_column(Integer)
    filer_name: Mapped[str] = mapped_column(String(200))
    accession: Mapped[str] = mapped_column(String(25))
    filing_date: Mapped[date] = mapped_column(Date)
    shares: Mapped[Decimal] = mapped_column(Numeric(24, 0))
    value_usd: Mapped[Decimal] = mapped_column(Numeric(24, 0))
    fetch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_fetches.id", ondelete="RESTRICT")
    )
