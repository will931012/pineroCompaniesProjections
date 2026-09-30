from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.fundamentals import FORMULA_VERSION
from app.auth.dependencies import AppSettings, CurrentUser, DbSession
from app.companies.routes import SecClient
from app.companies.service import resolve_security
from app.core.errors import ApiError
from app.db.models import Company, CompanyMetric, ProviderFetch, Security
from app.fundamentals.concepts import LINE_ITEMS, MAPPING_VERSION
from app.fundamentals.metrics import METRICS_BY_KEY, metric_series
from app.fundamentals.schemas import (
    CellOut,
    CompanyMetricsResponse,
    FundamentalsResponse,
    LineItemOut,
    MetricPointOut,
    MetricSeriesOut,
    PeerRow,
    PeersResponse,
    PeriodOut,
    SnapshotMetricOut,
    StatementsOut,
)
from app.fundamentals.service import load_facts, refresh_fundamentals
from app.fundamentals.snapshot import SCREEN_METRICS_BY_KEY
from app.fundamentals.statements import build_statements
from app.providers.schemas import source_refs

router = APIRouter(prefix="/companies", tags=["fundamentals"])

PEER_COLUMNS = [
    "market_cap",
    "revenue",
    "revenue_growth_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "roic",
    "debt_to_equity",
    "pe_ratio",
    "ev_to_ebitda",
    "fcf_yield",
]
MAX_EXTRA_PEERS = 10


@router.get("/{ticker}/fundamentals", response_model=FundamentalsResponse)
def fundamentals(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    period: Literal["annual", "quarterly"] = "annual",
    as_of: Annotated[date | None, Query(description="Only data public on or before")] = None,
    limit: Annotated[int, Query(ge=1, le=40)] = 10,
) -> FundamentalsResponse:
    security = resolve_security(db, ticker)
    company = security.company
    # Point-in-time requests never trigger a refresh; they read what is stored.
    status, message = (
        refresh_fundamentals(db, company, sec, settings) if as_of is None else ("current", None)
    )
    statements = build_statements(load_facts(db, company), period, as_of)
    statements.periods = statements.periods[-limit:]
    shown = {p.key for p in statements.periods}
    series = metric_series(statements)

    def items(statement: str) -> list[LineItemOut]:
        out: list[LineItemOut] = []
        for item in LINE_ITEMS:
            if item.statement != statement:
                continue
            cells = [
                CellOut(
                    period_key=key,
                    value=float(cell.value),
                    concept=cell.concept,
                    accession=cell.accession,
                    filed_date=cell.filed_date,
                    derivation=cell.derivation,
                )
                for key, cell in statements.cells.get(item.key, {}).items()
                if key in shown
            ]
            if cells:
                out.append(LineItemOut(key=item.key, label=item.label, unit=item.unit, cells=cells))
        return out

    fetch = (
        db.get(ProviderFetch, company.fundamentals_fetch_id)
        if company.fundamentals_fetch_id
        else None
    )
    return FundamentalsResponse(
        ticker=security.ticker,
        cik=company.cik,
        period_type=period,
        as_of=as_of,
        status=status,  # type: ignore[arg-type]
        message=message,
        mapping_version=MAPPING_VERSION,
        formula_version=FORMULA_VERSION,
        periods=[
            PeriodOut(
                key=p.key,
                label=p.label,
                fiscal_year=p.fiscal_year,
                fiscal_quarter=p.fiscal_quarter,
                start=p.start,
                end=p.end,
            )
            for p in statements.periods
        ],
        statements=StatementsOut(
            income=items("income"), balance=items("balance"), cash_flow=items("cash_flow")
        ),
        metrics=[
            MetricSeriesOut(
                key=key,
                label=METRICS_BY_KEY[key].label,
                category=METRICS_BY_KEY[key].category,
                unit=METRICS_BY_KEY[key].unit,
                formula=METRICS_BY_KEY[key].formula,
                values=[
                    MetricPointOut(period_key=k, value=float(v))
                    for k, v in values.items()
                    if k in shown
                ],
            )
            for key, values in series.items()
        ],
        sources=source_refs([fetch] if fetch else []),
    )


def _snapshot(db: Session, company_ids: list[int]) -> dict[int, dict[str, CompanyMetric]]:
    rows = db.scalars(select(CompanyMetric).where(CompanyMetric.company_id.in_(company_ids)))
    result: dict[int, dict[str, CompanyMetric]] = {}
    for row in rows:
        result.setdefault(row.company_id, {})[row.metric] = row
    return result


@router.get("/{ticker}/metrics", response_model=CompanyMetricsResponse)
def latest_metrics(ticker: str, _: CurrentUser, db: DbSession) -> CompanyMetricsResponse:
    security = resolve_security(db, ticker)
    rows = _snapshot(db, [security.company_id]).get(security.company_id, {})
    metrics = [
        SnapshotMetricOut(
            key=key,
            label=definition.label,
            category=definition.category,
            unit=definition.unit,
            description=definition.description,
            value=float(rows[key].value),
            basis=rows[key].basis,
            period_end=rows[key].period_end,
            available_date=rows[key].available_date,
            price_based=rows[key].derived_from_price,
        )
        for key, definition in SCREEN_METRICS_BY_KEY.items()
        if key in rows
    ]
    any_row = next(iter(rows.values()), None)
    return CompanyMetricsResponse(
        ticker=security.ticker,
        computed_at=any_row.computed_at if any_row else None,
        formula_version=any_row.formula_version if any_row else None,
        metrics=metrics,
    )


def _primary_listing(db: Session, company_ids: list[int]) -> dict[int, Security]:
    listings = db.scalars(
        select(Security)
        .where(Security.company_id.in_(company_ids), Security.is_active)
        .order_by(Security.company_id, Security.id)
    )
    result: dict[int, Security] = {}
    for listing in listings:
        result.setdefault(listing.company_id, listing)
    return result


@router.get("/{ticker}/peers", response_model=PeersResponse)
def peers(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    tickers: Annotated[str | None, Query(description="Comma-separated extra peers")] = None,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> PeersResponse:
    security = resolve_security(db, ticker)
    subject = security.company
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
    extra = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()]
    if len(extra) > MAX_EXTRA_PEERS:
        raise ApiError(422, "too_many_peers", f"At most {MAX_EXTRA_PEERS} extra peers.")
    for extra_ticker in extra:
        company_id = resolve_security(db, extra_ticker).company_id
        if company_id not in peer_ids and company_id != subject.id:
            peer_ids.append(company_id)

    ordered = [subject.id, *peer_ids]
    listings = _primary_listing(db, ordered)
    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(ordered)))}
    snapshot = _snapshot(db, ordered)
    rows = [
        PeerRow(
            ticker=security.ticker if cid == subject.id else listings[cid].ticker,
            name=companies[cid].legal_name,
            sic_code=companies[cid].sic_code,
            industry=companies[cid].industry,
            is_subject=cid == subject.id,
            metrics={
                k: float(m.value) for k, m in snapshot.get(cid, {}).items() if k in PEER_COLUMNS
            },
        )
        for cid in ordered
        if cid in listings or cid == subject.id
    ]
    return PeersResponse(ticker=security.ticker, basis=basis, columns=PEER_COLUMNS, rows=rows)
