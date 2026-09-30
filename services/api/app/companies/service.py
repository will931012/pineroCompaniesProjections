import logging
from datetime import timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.companies.classification import sic_division
from app.companies.schemas import (
    Classification,
    CompanyProfile,
    CompanySearchResponse,
    CompanySummary,
    DataAvailability,
    FormerName,
    ListingOut,
    ProfileStatus,
    SourceRef,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import Company, ProviderFetch, Security
from app.filings.index import store_filing_index
from app.providers.base import ProviderError, record_fetch
from app.providers.market_data.registry import market_data_status
from app.providers.sec_edgar import SecEdgarClient, SubmissionProfile

logger = logging.getLogger(__name__)


def _summary(company: Company, security: Security) -> CompanySummary:
    return CompanySummary(
        ticker=security.ticker,
        name=company.legal_name,
        exchange=security.exchange,
        cik=company.cik,
        sector=company.sector,
        industry=company.industry,
        country=company.country,
        is_active=security.is_active,
    )


def search_companies(db: Session, query: str, limit: int) -> CompanySearchResponse:
    term = query.strip()
    if not term:
        return CompanySearchResponse(items=[], total=0)
    upper = term.upper()
    matches = or_(
        Security.ticker.istartswith(upper, autoescape=True),
        Company.legal_name.icontains(term, autoescape=True),
    )
    base = (
        select(Company, Security)
        .join(Security, Security.company_id == Company.id)
        .where(Security.is_active, matches)
    )
    rank = case(
        (Security.ticker == upper, 0),
        (Security.ticker.istartswith(upper, autoescape=True), 1),
        (Company.legal_name.istartswith(term, autoescape=True), 2),
        else_=3,
    )
    rows = db.execute(
        base.order_by(rank, func.length(Security.ticker), Security.ticker).limit(limit)
    ).all()
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    return CompanySearchResponse(items=[_summary(c, s) for c, s in rows], total=total)


def resolve_security(db: Session, ticker: str) -> Security:
    """Active listing for a ticker; falls back to the most recently seen inactive one."""

    security = db.scalar(
        select(Security)
        .where(Security.ticker == ticker.strip().upper())
        .order_by(
            Security.is_active.desc(),
            Security.last_seen_at.desc().nulls_last(),
            Security.exchange.asc().nulls_last(),
        )
        .limit(1)
    )
    if security is None:
        raise ApiError(404, "company_not_found", "No company record exists for this ticker.")
    return security


def apply_submission_profile(company: Company, profile: SubmissionProfile, fetch_id: int) -> None:
    if profile.name:
        company.legal_name = profile.name
    company.entity_type = profile.entity_type
    company.classification_system = "SIC" if profile.sic_code else None
    company.sic_code = profile.sic_code
    company.sector = sic_division(profile.sic_code)
    company.industry = profile.sic_description
    company.filer_category = profile.filer_category
    company.state_of_incorporation = profile.state_of_incorporation
    company.fiscal_year_end = profile.fiscal_year_end
    company.website = profile.website
    company.description = profile.description
    company.hq_city = profile.hq_city
    company.hq_region = profile.hq_region
    # EDGAR flags whether the business address is outside the U.S.; nothing more
    # specific is mapped to ISO codes here.
    company.country = "US" if profile.hq_is_foreign is False else None
    company.former_names = profile.former_names
    company.profile_fetch_id = fetch_id
    company.profile_refreshed_at = utcnow()


def refresh_company_profile(
    db: Session,
    company: Company,
    client: SecEdgarClient,
    settings: Settings,
    *,
    force: bool = False,
) -> tuple[ProfileStatus, str | None]:
    """Refresh SEC submissions metadata when it is older than the configured TTL."""

    if company.cik is None:
        return "not_applicable", "This company has no SEC CIK."
    ttl = timedelta(hours=settings.sec_profile_ttl_hours)
    fresh = company.profile_refreshed_at is not None and (
        utcnow() - company.profile_refreshed_at < ttl
    )
    if fresh and not force:
        return "current", None
    if not client.configured:
        status: ProfileStatus = "stale" if company.profile_refreshed_at else "not_configured"
        return status, "SEC_USER_AGENT is not set, so SEC profile data cannot be refreshed."
    try:
        result = client.fetch_submissions(company.cik)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        status = "stale" if company.profile_refreshed_at else "unavailable"
        return status, f"SEC profile refresh failed: {error.message}"
    fetch = record_fetch(
        db,
        result.meta,
        status="success",
        record_count=len(result.data.filings),
        rejected_count=result.rejected_count,
    )
    apply_submission_profile(company, result.data.profile, fetch.id)
    # The same response lists the company's recent filings; index them too.
    store_filing_index(db, company, result.data.filings, fetch.id)
    company.filings_refreshed_at = utcnow()
    db.commit()
    return "current", None


def _source(fetch: ProviderFetch | None) -> SourceRef | None:
    if fetch is None:
        return None
    return SourceRef(
        fetch_id=fetch.id,
        provider=fetch.provider,
        dataset=fetch.dataset,
        source_url=fetch.source_url,
        retrieved_at=fetch.retrieved_at,
        license_note=fetch.license_note,
    )


def build_company_profile(
    db: Session, security: Security, status: ProfileStatus, message: str | None, settings: Settings
) -> CompanyProfile:
    company = security.company
    fetch_ids = {company.directory_fetch_id, company.profile_fetch_id} - {None}
    fetches = (
        db.scalars(select(ProviderFetch).where(ProviderFetch.id.in_(fetch_ids))).all()
        if fetch_ids
        else []
    )
    sources = [ref for ref in (_source(f) for f in fetches) if ref is not None]
    headquarters = ", ".join(part for part in (company.hq_city, company.hq_region) if part) or None
    market = market_data_status(settings)
    return CompanyProfile(
        ticker=security.ticker,
        name=company.legal_name,
        exchange=security.exchange,
        cik=company.cik,
        country=company.country,
        classification=(
            Classification(
                system="SIC",
                code=company.sic_code,
                sector=company.sector,
                industry=company.industry,
            )
            if company.sic_code
            else None
        ),
        entity_type=company.entity_type,
        filer_category=company.filer_category,
        state_of_incorporation=company.state_of_incorporation,
        fiscal_year_end=company.fiscal_year_end,
        website=company.website,
        description=company.description,
        headquarters=headquarters,
        former_names=[
            FormerName(name=item["name"], date_from=item.get("from"), date_to=item.get("to"))
            for item in company.former_names
        ],
        listings=[
            ListingOut(
                ticker=s.ticker,
                exchange=s.exchange,
                is_active=s.is_active,
                first_seen_at=s.first_seen_at,
                last_seen_at=s.last_seen_at,
            )
            for s in sorted(company.securities, key=lambda s: (not s.is_active, s.ticker))
        ],
        profile_status=status,
        profile_message=message,
        sources=sorted(sources, key=lambda ref: ref.fetch_id),
        availability={
            "market_data": DataAvailability(
                status="available" if market.configured else "not_configured",
                detail=market.message,
            ),
            "fundamentals": DataAvailability(
                status="available"
                if company.fundamentals_refreshed_at or settings.sec_user_agent
                else "not_configured",
                detail="SEC XBRL company facts"
                if company.cik
                else "No SEC CIK; SEC financial data unavailable",
            ),
            "sec_filings": DataAvailability(status="planned", detail="Phase 3"),
            "news": DataAvailability(status="planned", detail="Phase 4"),
            "valuation": DataAvailability(status="planned", detail="Phase 5"),
            "quant_models": DataAvailability(status="planned", detail="Phase 6"),
        },
    )
