from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.orm import aliased

from app.auth.dependencies import CurrentUser, DbSession
from app.core.errors import ApiError
from app.db.models import Company, CompanyMetric, Security
from app.fundamentals.snapshot import SCREEN_METRICS, SCREEN_METRICS_BY_KEY

router = APIRouter(prefix="/screener", tags=["screener"])

DEFAULT_COLUMNS = ["market_cap", "revenue", "revenue_growth_yoy", "operating_margin", "roic"]
MAX_FILTERS = 12


class ScreenMetricOut(BaseModel):
    key: str
    label: str
    category: str
    unit: Literal["percent", "ratio", "currency", "multiple"]
    description: str
    price_based: bool


class Filter(BaseModel):
    metric: str
    # Percent metrics are fractions: 15% is 0.15.
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _check(self) -> "Filter":
        if self.metric not in SCREEN_METRICS_BY_KEY:
            raise ValueError(f"Unknown metric '{self.metric}'.")
        if self.min is None and self.max is None:
            raise ValueError("A filter needs min, max, or both.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not exceed max.")
        return self


class ScreenRequest(BaseModel):
    filters: list[Filter] = Field(default_factory=list, max_length=MAX_FILTERS)
    sector: str | None = Field(default=None, max_length=120)
    sort_by: str = "market_cap"
    sort_dir: Literal["asc", "desc"] = "desc"
    columns: list[str] = Field(default_factory=lambda: list(DEFAULT_COLUMNS), max_length=15)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10_000)


class ScreenRow(BaseModel):
    ticker: str
    name: str
    exchange: str | None
    sector: str | None
    industry: str | None
    metrics: dict[str, float]
    metrics_as_of: dict[str, date | None]


class ScreenResponse(BaseModel):
    total: int
    columns: list[str]
    rows: list[ScreenRow]
    universe: int


@router.get("/metrics", response_model=list[ScreenMetricOut])
def metric_catalog(_: CurrentUser) -> list[ScreenMetricOut]:
    return [
        ScreenMetricOut(
            key=m.key,
            label=m.label,
            category=m.category,
            unit=m.unit,
            description=m.description,
            price_based=m.price_based,
        )
        for m in SCREEN_METRICS
    ]


@router.get("/sectors", response_model=list[str])
def sectors(_: CurrentUser, db: DbSession) -> list[str]:
    return [
        s
        for s in db.scalars(
            select(Company.sector)
            .where(Company.sector.is_not(None))
            .distinct()
            .order_by(Company.sector)
        )
        if s
    ]


def _primary_listing() -> Select:
    """One active listing per company (lowest id), so dual-class issuers appear once."""
    return (
        select(Security.company_id, func.min(Security.id).label("security_id"))
        .where(Security.is_active)
        .group_by(Security.company_id)
    )


@router.post("", response_model=ScreenResponse)
def screen(body: ScreenRequest, _: CurrentUser, db: DbSession) -> ScreenResponse:
    unknown = [c for c in [*body.columns, body.sort_by] if c not in SCREEN_METRICS_BY_KEY]
    if unknown:
        raise ApiError(422, "unknown_metric", f"Unknown metric: {', '.join(sorted(set(unknown)))}.")

    listing = _primary_listing().subquery()
    # The universe is companies with computed metrics; others have nothing to screen on.
    has_metrics = exists().where(CompanyMetric.company_id == Company.id)
    query = select(Company.id).join(listing, listing.c.company_id == Company.id).where(has_metrics)
    if body.sector:
        query = query.where(Company.sector == body.sector)
    for f in body.filters:
        alias = aliased(CompanyMetric)
        conditions = [alias.company_id == Company.id, alias.metric == f.metric]
        if f.min is not None:
            conditions.append(alias.value >= f.min)
        if f.max is not None:
            conditions.append(alias.value <= f.max)
        query = query.join(alias, and_(*conditions))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    universe = (
        db.scalar(
            select(func.count()).select_from(select(CompanyMetric.company_id).distinct().subquery())
        )
        or 0
    )

    sort = aliased(CompanyMetric)
    order = (
        sort.value.desc().nulls_last() if body.sort_dir == "desc" else sort.value.asc().nulls_last()
    )
    page = db.execute(
        query.add_columns(listing.c.security_id)
        .outerjoin(sort, and_(sort.company_id == Company.id, sort.metric == body.sort_by))
        .order_by(order, Company.legal_name)
        .limit(body.limit)
        .offset(body.offset)
    ).all()
    company_ids = [row[0] for row in page]
    security_ids = [row[1] for row in page]

    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(company_ids)))}
    securities = {
        s.id: s for s in db.scalars(select(Security).where(Security.id.in_(security_ids)))
    }
    wanted = list(dict.fromkeys([*body.columns, *(f.metric for f in body.filters), body.sort_by]))
    metrics: dict[int, dict[str, CompanyMetric]] = {}
    for row in db.scalars(
        select(CompanyMetric).where(
            CompanyMetric.company_id.in_(company_ids), CompanyMetric.metric.in_(wanted)
        )
    ):
        metrics.setdefault(row.company_id, {})[row.metric] = row

    rows = []
    for company_id, security_id in zip(company_ids, security_ids, strict=True):
        company, security = companies[company_id], securities[security_id]
        values = metrics.get(company_id, {})
        rows.append(
            ScreenRow(
                ticker=security.ticker,
                name=company.legal_name,
                exchange=security.exchange,
                sector=company.sector,
                industry=company.industry,
                metrics={k: float(v.value) for k, v in values.items()},
                metrics_as_of={k: v.available_date for k, v in values.items()},
            )
        )
    return ScreenResponse(total=total, columns=wanted, rows=rows, universe=universe)
