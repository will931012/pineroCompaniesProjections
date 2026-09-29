"""Synchronise the company/security directory with SEC's official ticker file."""

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import Company, ProviderFetch, Security
from app.providers.base import ProviderError, record_fetch
from app.providers.sec_edgar import PROVIDER, SecEdgarClient

logger = logging.getLogger(__name__)

# A snapshot this much smaller than the current active set is treated as a partial
# download, and nothing is deactivated.
MIN_SNAPSHOT_RATIO = 0.5


@dataclass
class DirectorySyncSummary:
    fetch_id: int
    entries: int
    rejected: int
    duplicates: int
    companies_created: int
    companies_renamed: int
    securities_created: int
    securities_reassigned: int
    securities_deactivated: int
    deactivation_skipped: bool

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def sync_sec_directory(db: Session, client: SecEdgarClient) -> DirectorySyncSummary:
    try:
        result = client.fetch_directory()
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        raise

    now = utcnow()
    fetch = record_fetch(
        db,
        result.meta,
        status="success",
        record_count=len(result.data),
        rejected_count=result.rejected_count,
    )

    companies = {c.cik: c for c in db.scalars(select(Company).where(Company.cik.is_not(None)))}
    active = {
        (s.ticker, s.exchange): s for s in db.scalars(select(Security).where(Security.is_active))
    }
    cik_by_company_id = {c.id: c.cik for c in companies.values()}
    sec_fetch_ids = set(
        db.scalars(select(ProviderFetch.id).where(ProviderFetch.provider == PROVIDER))
    )
    previously_listed = sum(1 for s in active.values() if s.source_fetch_id in sec_fetch_ids)
    counts = dict.fromkeys(
        (
            "companies_created",
            "companies_renamed",
            "securities_created",
            "securities_reassigned",
            "securities_deactivated",
            "duplicates",
        ),
        0,
    )
    seen: set[tuple[str, str | None]] = set()

    for entry in result.data:
        key = (entry.ticker, entry.exchange)
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)

        company = companies.get(entry.cik)
        if company is None:
            company = Company(cik=entry.cik, legal_name=entry.name, directory_fetch_id=fetch.id)
            db.add(company)
            companies[entry.cik] = company
            counts["companies_created"] += 1
        else:
            if company.profile_refreshed_at is None and company.legal_name != entry.name:
                company.legal_name = entry.name
                counts["companies_renamed"] += 1
            company.directory_fetch_id = fetch.id

        security = active.get(key)
        if security is not None and cik_by_company_id.get(security.company_id) != entry.cik:
            # The ticker moved to another registrant: retire the old listing, keep its history.
            security.is_active = False
            counts["securities_reassigned"] += 1
            security = None
        if security is None:
            security = Security(
                company=company,
                ticker=entry.ticker,
                exchange=entry.exchange,
                is_active=True,
                first_seen_at=now,
                last_seen_at=now,
                source_fetch_id=fetch.id,
            )
            db.add(security)
            active[key] = security
            counts["securities_created"] += 1
        else:
            security.last_seen_at = now
            security.source_fetch_id = fetch.id
        if len(seen) % 2000 == 0:
            db.flush()

    missing = [
        s
        for k, s in active.items()
        if s.is_active and k not in seen and s.source_fetch_id in sec_fetch_ids
    ]
    skip_deactivation = len(seen) < previously_listed * MIN_SNAPSHOT_RATIO
    if skip_deactivation:
        logger.warning(
            "sec_directory_snapshot_too_small",
            extra={"snapshot": len(seen), "active": previously_listed},
        )
    else:
        for security in missing:
            security.is_active = False
            counts["securities_deactivated"] += 1

    db.commit()
    summary = DirectorySyncSummary(
        fetch_id=fetch.id,
        entries=len(result.data),
        rejected=result.rejected_count,
        deactivation_skipped=skip_deactivation,
        **counts,
    )
    logger.info("sec_directory_synced", extra=summary.as_dict())
    return summary
