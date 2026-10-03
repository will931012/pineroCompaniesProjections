"""The model universe: the largest companies by revenue as it was known on each date.

1. Candidates come from SEC's XBRL frames: every company ranked in the top
   `CANDIDATE_RANK` by calendar-year revenue in any year. Frames hold the latest filed values
   and are used only to decide whose filings to load, never for ranking.
2. Each candidate's facts are loaded (Phase 2 point-in-time store). Companies no longer in
   SEC's ticker directory (delisted, acquired) are kept as companies without listings.
3. On each month end, candidates are ranked by their latest fiscal-year revenue whose filing
   was public by that date. The top `settings.universe_size` form the universe; members
   without price history stay in the table with `has_prices = false`, which is how
   survivorship bias is measured.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import (
    Company,
    FinancialFact,
    PriceCoverage,
    Security,
    UniverseCandidate,
    UniverseMember,
)
from app.fundamentals.concepts import LINE_ITEMS
from app.providers.base import ProviderError, record_fetch
from app.providers.sec_edgar import SecEdgarClient

logger = logging.getLogger(__name__)

UNIVERSE_VERSION = "u1"
CANDIDATE_RANK = 300
FIRST_YEAR = 2011
REVENUE_CONCEPTS = next(item.concepts for item in LINE_ITEMS if item.key == "revenue")
ANNUAL_DAYS = (330, 400)


def month_ends(start: date, end: date) -> list[date]:
    """Calendar month ends from `start`'s month through the last month ending on or before `end`."""
    out: list[date] = []
    year, month = start.year, start.month
    while True:
        following = date(year + (month == 12), month % 12 + 1, 1)
        last = following - timedelta(days=1)
        if last > end:
            return out
        out.append(last)
        year, month = following.year, following.month


@dataclass
class CandidateReport:
    frames: int = 0
    ranked_ciks: int = 0
    candidates: int = 0
    created_companies: int = 0


def build_candidates(
    db: Session, client: SecEdgarClient, last_year: int, first_year: int = FIRST_YEAR
) -> CandidateReport:
    """Rank companies by calendar-year revenue in each year's frames; keep the top ones."""
    report = CandidateReport()
    best: dict[int, tuple[int, int, str]] = {}  # cik -> (rank, year, name)
    for year in range(first_year, last_year + 1):
        revenue: dict[int, tuple[float, str]] = {}
        for concept in REVENUE_CONCEPTS:
            try:
                result = client.fetch_frame(concept, "USD", f"CY{year}")
            except ProviderError as error:
                if error.meta is not None:
                    record_fetch(db, error.meta, status="error", error=error)
                    db.commit()
                raise
            record_fetch(
                db, result.meta, status="success", record_count=len(result.data),
                rejected_count=result.rejected_count,
            )  # fmt: skip
            report.frames += 1
            for fact in result.data:
                current = revenue.get(fact.cik)
                if current is None or fact.value > current[0]:
                    revenue[fact.cik] = (fact.value, fact.entity_name)
        db.commit()
        ranked = sorted(revenue.items(), key=lambda kv: -kv[1][0])[:CANDIDATE_RANK]
        for rank, (cik, (_, name)) in enumerate(ranked, start=1):
            if cik not in best or rank < best[cik][0]:
                best[cik] = (rank, year, name)
    report.ranked_ciks = len(best)

    companies = {c.cik: c for c in db.scalars(select(Company).where(Company.cik.in_(best)))}
    for cik, (_, _, name) in best.items():
        if cik not in companies:
            # Delisted or renamed: keep the registrant so its filings can still be ranked.
            company = Company(cik=cik, legal_name=name or f"CIK {cik}")
            db.add(company)
            companies[cik] = company
            report.created_companies += 1
    db.flush()
    rows = [
        {"company_id": companies[cik].id, "best_rank": rank, "best_year": year}
        for cik, (rank, year, _) in best.items()
    ]
    statement = insert(UniverseCandidate).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["company_id"],
            set_={
                "best_rank": statement.excluded.best_rank,
                "best_year": statement.excluded.best_year,
            },
        )
    )
    db.commit()
    report.candidates = len(rows)
    return report


@dataclass(frozen=True)
class AnnualRevenue:
    period_end: date
    filed: date
    value: Decimal
    concept: str


def revenue_timelines(db: Session, company_ids: list[int]) -> dict[int, list[AnnualRevenue]]:
    """Annual revenue facts per company, in filing order (restatements are later rows)."""
    rows = db.execute(
        select(
            FinancialFact.company_id, FinancialFact.concept, FinancialFact.period_start,
            FinancialFact.period_end, FinancialFact.filed_date, FinancialFact.value,
        )
        .where(
            FinancialFact.company_id.in_(company_ids),
            FinancialFact.concept.in_(REVENUE_CONCEPTS),
            FinancialFact.unit == "USD",
            FinancialFact.period_start.is_not(None),
        )
        .order_by(FinancialFact.filed_date)
    ).all()  # fmt: skip
    out: dict[int, list[AnnualRevenue]] = defaultdict(list)
    for r in rows:
        days = (r.period_end - r.period_start).days
        if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1] and r.value > 0:
            out[r.company_id].append(AnnualRevenue(r.period_end, r.filed_date, r.value, r.concept))
    return out


def revenue_as_of(timeline: list[AnnualRevenue], day: date) -> AnnualRevenue | None:
    """Latest fiscal year public by `day`; within it, the concept earliest in the priority
    list, and that concept's latest filed value (so restatements count once known)."""
    known = [f for f in timeline if f.filed <= day]
    if not known:
        return None
    period = max(f.period_end for f in known)
    if period < day - timedelta(days=550):  # stopped filing: no longer a going concern here
        return None
    in_period = [f for f in known if f.period_end == period]
    concept = min(in_period, key=lambda f: REVENUE_CONCEPTS.index(f.concept)).concept
    return [f for f in in_period if f.concept == concept][-1]


def rank_universe(db: Session, settings: Settings, dates: list[date]) -> dict[str, int]:
    """Rebuild universe membership for `dates` from the candidates' stored filings."""
    candidate_ids = list(db.scalars(select(UniverseCandidate.company_id)))
    timelines = revenue_timelines(db, candidate_ids)
    listings = _primary_listings(db, candidate_ids)
    successors = successor_listings(db, candidate_ids, listings)
    for company_id, (security_id, _) in successors.items():
        listings[company_id] = security_id
    coverage = _coverage(db, [s for s in listings.values() if s is not None])
    db.execute(
        delete(UniverseMember).where(
            UniverseMember.version == UNIVERSE_VERSION, UniverseMember.as_of.in_(dates)
        )
    )
    total = 0
    for day in dates:
        ranked = sorted(
            (
                (company_id, revenue)
                for company_id, timeline in timelines.items()
                if (revenue := revenue_as_of(timeline, day)) is not None
            ),
            key=lambda item: -item[1].value,
        )[: settings.universe_size]
        rows = []
        used: set[int] = set()
        for rank, (company_id, revenue) in enumerate(ranked, start=1):
            security = listings.get(company_id)
            basis = f"FY ending {revenue.period_end.isoformat()}"
            if company_id in successors:
                basis += f"; prices of successor listing {successors[company_id][1]}"
            if security is not None and security in used:
                # A predecessor and its successor both ranked: one listing, counted once.
                security, basis = None, f"{basis}; listing already counted"
            if security is not None:
                used.add(security)
            first = coverage.get(security) if security else None
            rows.append(
                {
                    "version": UNIVERSE_VERSION, "as_of": day, "company_id": company_id,
                    "rank": rank, "revenue_ttm": revenue.value, "revenue_basis": basis[:40],
                    "security_id": security,
                    "has_prices": first is not None and first <= day - timedelta(days=370),
                }
            )  # fmt: skip
        if rows:
            db.execute(insert(UniverseMember).values(rows))
        total += len(rows)
    db.commit()
    return {"dates": len(dates), "rows": total}


def refresh_has_prices(db: Session) -> None:
    """Re-mark which members have a year of prices before each date (after price loads)."""
    members = db.scalars(
        select(UniverseMember).where(
            UniverseMember.version == UNIVERSE_VERSION, UniverseMember.security_id.is_not(None)
        )
    ).all()
    coverage = _coverage(db, list({m.security_id for m in members if m.security_id}))
    for member in members:
        first = coverage.get(member.security_id) if member.security_id else None
        member.has_prices = first is not None and first <= member.as_of - timedelta(days=370)
    db.commit()


_NAME_SUFFIX = re.compile(
    r"\b(the|holdings?|corp(oration)?|inc(orporated)?|co(mpany)?|llc|ltd|plc|group|lp|new)\b"
)
SUCCESSION_WINDOW = (timedelta(days=-30), timedelta(days=120))


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _NAME_SUFFIX.sub(" ", name.lower()))


def successor_listings(
    db: Session, candidate_ids: list[int], listings: dict[int, int | None]
) -> dict[int, tuple[int, str]]:
    """Unlisted candidates whose filer was replaced by a listed successor registrant.

    Holding-company reorganisations and redomiciles (e.g. Exxon Mobil Corp → ExxonMobil
    Holdings Corp) move the same shares to a new SEC registrant. A successor is accepted only
    when its name matches after removing legal suffixes AND it began filing within 30 days
    before to 120 days after the predecessor's last filing; this rejects unrelated companies
    that reuse a name years later (e.g. Constellation Energy Group, 2012, and Constellation
    Energy Corp, 2022).
    """
    unlisted = [c for c in candidate_ids if listings.get(c) is None]
    if not unlisted:
        return {}
    names = dict(
        db.execute(select(Company.id, Company.legal_name).where(Company.id.in_(unlisted))).all()
    )
    listed = db.execute(
        select(Security.company_id, Security.id, Security.ticker, Company.legal_name)
        .join(Company, Company.id == Security.company_id)
        .where(Security.is_active)
        .order_by(func.length(Security.ticker), Security.ticker)
    ).all()
    by_name: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in listed:
        key = normalize_name(row.legal_name)
        if key and all(row.company_id != c for c, _, _ in by_name[key]):
            by_name[key].append((row.company_id, row.id, row.ticker))
    involved = {c for c in unlisted} | {c for rows in by_name.values() for c, _, _ in rows}
    spans = {
        r[0]: (r[1], r[2])
        for r in db.execute(
            select(FinancialFact.company_id, func.min(FinancialFact.filed_date),
                   func.max(FinancialFact.filed_date))
            .where(FinancialFact.company_id.in_(involved))
            .group_by(FinancialFact.company_id)
        ).all()
    }  # fmt: skip
    out: dict[int, tuple[int, str]] = {}
    for company_id in unlisted:
        old = spans.get(company_id)
        if old is None:
            continue
        for successor, security_id, ticker in by_name.get(normalize_name(names[company_id]), []):
            new = spans.get(successor)
            if new is None:
                continue
            gap = new[0] - old[1]
            if SUCCESSION_WINDOW[0] <= gap <= SUCCESSION_WINDOW[1]:
                out[company_id] = (security_id, ticker)
                break
    return out


def _primary_listings(db: Session, company_ids: list[int]) -> dict[int, int | None]:
    """Active listing per company (shortest ticker first, e.g. GOOGL before GOOGL-W)."""
    rows = db.execute(
        select(Security.company_id, Security.id, Security.ticker)
        .where(Security.company_id.in_(company_ids), Security.is_active)
        .order_by(Security.company_id, func.length(Security.ticker), Security.ticker)
    ).all()
    out: dict[int, int | None] = {}
    for r in rows:
        out.setdefault(r.company_id, r.id)
    return out


def _coverage(db: Session, security_ids: list[int]) -> dict[int, date]:
    """First loaded bar per security."""
    if not security_ids:
        return {}
    rows = db.execute(
        select(PriceCoverage.security_id, PriceCoverage.loaded_from).where(
            PriceCoverage.security_id.in_(security_ids),
            PriceCoverage.status == "loaded",
            PriceCoverage.loaded_from.is_not(None),
        )
    ).all()
    return {r.security_id: r.loaded_from for r in rows}


def price_targets(db: Session) -> list[Security]:
    """Listings the bulk loader keeps current: every universe member ever, by best rank."""
    ids = db.scalars(
        select(UniverseMember.security_id)
        .where(UniverseMember.version == UNIVERSE_VERSION, UniverseMember.security_id.is_not(None))
        .group_by(UniverseMember.security_id)
        .order_by(func.min(UniverseMember.rank))
    ).all()
    securities = {s.id: s for s in db.scalars(select(Security).where(Security.id.in_(ids)))}
    return [securities[i] for i in ids if i in securities]


def survivorship(db: Session) -> list[dict[str, object]]:
    """Per date: members, members with prices, and the share missing prices."""
    rows = db.execute(
        select(
            UniverseMember.as_of,
            func.count(),
            func.count().filter(UniverseMember.has_prices),
            func.count().filter(UniverseMember.security_id.is_(None)),
        )
        .where(UniverseMember.version == UNIVERSE_VERSION)
        .group_by(UniverseMember.as_of)
        .order_by(UniverseMember.as_of)
    ).all()
    return [
        {
            "as_of": r[0], "members": r[1], "with_prices": r[2], "no_listing": r[3],
            "missing_share": 1 - r[2] / r[1] if r[1] else None,
        }
        for r in rows
    ]  # fmt: skip
