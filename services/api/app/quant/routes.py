import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, DbSession
from app.companies.service import resolve_security
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.models import ModelVersion
from app.quant import schemas as s
from app.quant import views

router = APIRouter(tags=["quant"])


@router.get("/markets/overview", response_model=s.MarketsOut)
def markets_overview(_: CurrentUser, db: DbSession) -> s.MarketsOut:
    return views.markets_overview(db, get_settings())


@router.get("/bitcoin", response_model=s.BitcoinOut)
def bitcoin(_: CurrentUser, db: DbSession) -> s.BitcoinOut:
    return views.bitcoin_view(db)


@router.get("/companies/{ticker}/technicals", response_model=s.TechnicalsOut)
def company_technicals(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    years: Annotated[int, Query(ge=1, le=15)] = 3,
) -> s.TechnicalsOut:
    return views.technicals(db, resolve_security(db, ticker), years)


@router.get("/companies/{ticker}/factors", response_model=s.FactorsOut)
def company_factors(ticker: str, _: CurrentUser, db: DbSession) -> s.FactorsOut:
    security = resolve_security(db, ticker)
    return views.factors(db, security.company).model_copy(update={"ticker": security.ticker})


@router.get("/companies/{ticker}/ownership", response_model=s.OwnershipOut)
def company_ownership(ticker: str, _: CurrentUser, db: DbSession) -> s.OwnershipOut:
    security = resolve_security(db, ticker)
    return views.ownership(db, security.company).model_copy(update={"ticker": security.ticker})


@router.get("/companies/{ticker}/predictions", response_model=s.PredictionsOut)
def company_predictions(ticker: str, _: CurrentUser, db: DbSession) -> s.PredictionsOut:
    security = resolve_security(db, ticker)
    return views.predictions(db, security.company).model_copy(update={"ticker": security.ticker})


@router.get("/models", response_model=list[s.ModelSummary])
def models(_: CurrentUser, db: DbSession) -> list[s.ModelSummary]:
    rows = db.scalars(
        select(ModelVersion).order_by(ModelVersion.status, ModelVersion.created_at.desc()).limit(20)
    )
    return [views.model_summary(db, v) for v in rows]


@router.get("/models/{model_id}", response_model=s.ModelDetail)
def model(model_id: uuid.UUID, _: CurrentUser, db: DbSession) -> s.ModelDetail:
    version = db.get(ModelVersion, model_id)
    if version is None:
        raise ApiError(404, "model_not_found", "No such model version.")
    return views.model_detail(db, version)


@router.get("/research/status", response_model=s.ResearchStatus)
def research_status(_: CurrentUser, db: DbSession) -> s.ResearchStatus:
    return views.research_status(db, get_settings())
