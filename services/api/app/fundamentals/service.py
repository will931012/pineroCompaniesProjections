import logging
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analytics.fundamentals import FORMULA_VERSION
from app.companies.service import refresh_company_profile
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Company, CompanyMetric, DailyPrice, FinancialFact, Security
from app.fundamentals.concepts import ACCEPTED_FORMS, LINE_ITEMS, SHARES_OUTSTANDING
from app.fundamentals.snapshot import PricePoint, compute_company_metrics
from app.providers.base import ProviderError, record_fetch
from app.providers.sec_edgar import SecEdgarClient

logger = logging.getLogger(__name__)

TRACKED_UNITS: dict[tuple[str, str], str] = {
    (item.taxonomy, concept): item.unit
    for item in (*LINE_ITEMS, SHARES_OUTSTANDING)
    for concept in item.concepts
}
_INSERT_CHUNK = 1000


def refresh_fundamentals(
    db: Session,
    company: Company,
    client: SecEdgarClient,
    settings: Settings,
    *,
    force: bool = False,
) -> tuple[str, str | None]:
    """Fetch SEC company facts when older than the TTL. Returns (status, message)."""

    if company.cik is None:
        return "not_applicable", "This company has no SEC CIK, so SEC financials are unavailable."
    ttl = timedelta(hours=settings.fundamentals_ttl_hours)
    if (
        not force
        and company.fundamentals_refreshed_at is not None
        and utcnow() - company.fundamentals_refreshed_at < ttl
    ):
        return "current", None
    if not client.configured:
        status = "stale" if company.fundamentals_refreshed_at else "not_configured"
        return status, "SEC_USER_AGENT is not set, so SEC financial data cannot be refreshed."
    try:
        result = client.fetch_company_facts(company.cik, TRACKED_UNITS, ACCEPTED_FORMS)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        if error.code == "provider_record_not_found":
            return "not_available", "SEC has no XBRL financial data for this registrant."
        status = "stale" if company.fundamentals_refreshed_at else "unavailable"
        return status, f"SEC financial data refresh failed: {error.message}"

    fetch = record_fetch(
        db,
        result.meta,
        status="success",
        record_count=len(result.data),
        rejected_count=result.rejected_count,
    )
    rows = [
        {
            "company_id": company.id,
            "taxonomy": o.taxonomy,
            "concept": o.concept,
            "unit": o.unit,
            "period_start": o.period_start,
            "period_end": o.period_end,
            "value": o.value,
            "fiscal_year": o.fiscal_year,
            "fiscal_period": o.fiscal_period,
            "form": o.form,
            "accession": o.accession,
            "filed_date": o.filed_date,
            "fetch_id": fetch.id,
        }
        for o in result.data
    ]
    for start in range(0, len(rows), _INSERT_CHUNK):
        # Existing observations keep their original fetch; only new values are added.
        db.execute(
            insert(FinancialFact)
            .values(rows[start : start + _INSERT_CHUNK])
            .on_conflict_do_nothing()
        )
    company.fundamentals_fetch_id = fetch.id
    company.fundamentals_refreshed_at = utcnow()
    refresh_company_metrics(db, company)
    db.commit()
    logger.info(
        "fundamentals_refreshed",
        extra={"cik": company.cik, "facts": len(rows), "rejected": result.rejected_count},
    )
    return "current", None


def sync_company(
    db: Session,
    company: Company,
    client: SecEdgarClient,
    settings: Settings,
    *,
    force: bool = False,
) -> tuple[str, str | None]:
    """Bulk ingestion for one company: SEC profile (within its TTL) then financial data.

    The profile supplies the SIC code that peer selection and screener sectors rely on, and
    is otherwise loaded only when someone opens the company page.
    """
    refresh_company_profile(db, company, client, settings)
    return refresh_fundamentals(db, company, client, settings, force=force)


def load_facts(db: Session, company: Company) -> Sequence[FinancialFact]:
    return db.scalars(select(FinancialFact).where(FinancialFact.company_id == company.id)).all()


def primary_security(db: Session, company: Company) -> Security | None:
    return db.scalar(
        select(Security)
        .where(Security.company_id == company.id)
        .order_by(Security.is_active.desc(), Security.id)
        .limit(1)
    )


def load_prices(db: Session, company: Company) -> list[PricePoint]:
    """Stored daily closes for the primary listing from the provider with the newest bar."""

    security = primary_security(db, company)
    if security is None:
        return []
    provider = db.scalar(
        select(DailyPrice.provider)
        .where(DailyPrice.security_id == security.id)
        .group_by(DailyPrice.provider)
        .order_by(func.max(DailyPrice.trade_date).desc())
        .limit(1)
    )
    if provider is None:
        return []
    rows = db.execute(
        select(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adj_close)
        .where(DailyPrice.security_id == security.id, DailyPrice.provider == provider)
        .order_by(DailyPrice.trade_date)
    ).all()
    return [PricePoint(r.trade_date, r.close, r.adj_close) for r in rows]


def refresh_company_metrics(db: Session, company: Company, as_of: date | None = None) -> int:
    """Recompute the company's latest screenable metrics in the caller's transaction."""

    values = compute_company_metrics(load_facts(db, company), load_prices(db, company), as_of)
    db.execute(delete(CompanyMetric).where(CompanyMetric.company_id == company.id))
    now = utcnow()
    db.add_all(
        CompanyMetric(
            company_id=company.id,
            metric=v.key,
            value=v.value,
            basis=v.basis[:160],
            period_end=v.period_end,
            available_date=v.available_date,
            derived_from_price=v.price_based,
            formula_version=FORMULA_VERSION,
            computed_at=now,
        )
        for v in values
    )
    db.flush()
    return len(values)
