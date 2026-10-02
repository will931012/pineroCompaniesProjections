"""Peer selection shared by the peers table and relative valuation."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.companies.service import resolve_security
from app.core.errors import ApiError
from app.db.models import Company, CompanyMetric, Security

MAX_EXTRA_PEERS = 10


def select_peers(
    db: Session, subject: Company, extra_tickers: list[str], limit: int
) -> tuple[str, list[int]]:
    """Companies sharing the subject's SIC code, or its two-digit major group when fewer than
    three do, ranked by revenue; then any explicitly requested tickers. Returns (basis, ids)."""
    basis = "Selected peers"
    peer_ids: list[int] = []
    if subject.sic_code:
        revenue = (
            select(CompanyMetric.value)
            .where(CompanyMetric.company_id == Company.id, CompanyMetric.metric == "revenue")
            .scalar_subquery()
        )
        for label, condition in (
            (f"SIC {subject.sic_code}", Company.sic_code == subject.sic_code),
            (
                f"SIC major group {subject.sic_code[:2]}",
                func.left(Company.sic_code, 2) == subject.sic_code[:2],
            ),
        ):
            peer_ids = list(
                db.scalars(
                    select(Company.id)
                    .join(Security, Security.company_id == Company.id)
                    .where(condition, Company.id != subject.id, Security.is_active)
                    .group_by(Company.id)
                    .order_by(revenue.desc().nulls_last(), Company.legal_name)
                    .limit(limit)
                )
            )
            basis = label
            if len(peer_ids) >= 3:
                break
    if len(extra_tickers) > MAX_EXTRA_PEERS:
        raise ApiError(422, "too_many_peers", f"At most {MAX_EXTRA_PEERS} extra peers.")
    for ticker in extra_tickers:
        company_id = resolve_security(db, ticker).company_id
        if company_id not in peer_ids and company_id != subject.id:
            peer_ids.append(company_id)
    return basis, peer_ids
