import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.valuation import FORMULA_VERSION, ValuationError
from app.auth.dependencies import CurrentUser, DbSession
from app.companies.service import resolve_security
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.models import Company, Security, User, ValuationRun
from app.fundamentals.service import load_prices
from app.providers.treasury import TreasuryClient, build_treasury_client
from app.valuation.compute import run
from app.valuation.relative import relative_valuation
from app.valuation.schemas import (
    CapmIn,
    DcfIn,
    DdmIn,
    DefaultsOut,
    RelativeOut,
    RimIn,
    RunOut,
    RunSummary,
    SaveRunIn,
    ScenariosIn,
    ValuationIn,
    ValuationOut,
)
from app.valuation.service import build_defaults

router = APIRouter(tags=["valuation"])


def get_treasury() -> TreasuryClient:
    return build_treasury_client(get_settings().sec_user_agent)


Treasury = Annotated[TreasuryClient, Depends(get_treasury)]


def _valuation(db: Session, ticker: str, request: ValuationIn) -> ValuationOut:
    security = resolve_security(db, ticker)
    prices = load_prices(db, security.company)
    price = float(prices[-1].close) if prices else None
    try:
        rates, scenarios, grids = run(request, price)
    except ValuationError as error:
        raise ApiError(422, "invalid_assumptions", str(error)) from error
    return ValuationOut(
        ticker=security.ticker,
        model=request.model,
        rates=rates,
        scenarios=scenarios,
        sensitivity=grids,
        price=price,
        price_date=prices[-1].trade_date if prices else None,
        formula_version=FORMULA_VERSION,
    )


@router.get("/companies/{ticker}/valuation/defaults", response_model=DefaultsOut)
def valuation_defaults(
    ticker: str, _: CurrentUser, db: DbSession, treasury: Treasury
) -> DefaultsOut:
    security = resolve_security(db, ticker)
    defaults = build_defaults(db, security.company, treasury)
    inputs = defaults.inputs
    return DefaultsOut(
        ticker=security.ticker,
        recommended=defaults.recommended,
        available=defaults.available,
        unavailable=defaults.unavailable,
        dcf=DcfIn(**inputs["dcf"]) if "dcf" in inputs else None,
        rim=RimIn(**inputs["rim"]) if "rim" in inputs else None,
        ddm=DdmIn(**inputs["ddm"]) if "ddm" in inputs else None,
        capm=CapmIn(**defaults.capm) if defaults.capm else None,
        scenarios=ScenariosIn(),
        sources=defaults.sources,
        warnings=defaults.warnings,
        price=defaults.price,
        price_date=defaults.price_date,
        basis=defaults.basis_label,
        formula_version=FORMULA_VERSION,
    )


@router.post("/companies/{ticker}/valuation/compute", response_model=ValuationOut)
def compute_valuation(
    ticker: str, body: ValuationIn, _: CurrentUser, db: DbSession
) -> ValuationOut:
    """Value the company with these assumptions without saving anything."""
    return _valuation(db, ticker, body)


def _summary(run_row: ValuationRun) -> RunSummary:
    base = next((s for s in run_row.results.get("scenarios", []) if s.get("name") == "base"), None)
    result = (base or {}).get("result") or {}
    return RunSummary(
        id=run_row.id,
        name=run_row.name,
        model=run_row.model,  # type: ignore[arg-type]
        created_at=run_row.created_at,
        base_per_share=result.get("per_share"),
        price=float(run_row.price) if run_row.price is not None else None,
        formula_version=run_row.formula_version,
    )


def _run_out(run_row: ValuationRun) -> RunOut:
    return RunOut(
        **_summary(run_row).model_dump(),
        request=run_row.assumptions,
        sources=run_row.sources,
        results=ValuationOut(**run_row.results),
    )


@router.post("/companies/{ticker}/valuation/runs", response_model=RunOut, status_code=201)
def save_run(ticker: str, body: SaveRunIn, user: CurrentUser, db: DbSession) -> RunOut:
    """Value and store the run with the exact assumptions and their sources."""
    output = _valuation(db, ticker, body)
    company_id = db.scalar(select(Security.company_id).where(Security.ticker == output.ticker))
    request = body.model_dump(exclude={"name", "sources"})
    row = ValuationRun(
        company_id=company_id,
        owner_id=user.id,
        name=body.name.strip(),
        model=body.model,
        assumptions=request,
        scenarios=request["scenarios"],
        sources=body.sources,
        results=output.model_dump(mode="json"),
        price=Decimal(str(output.price)) if output.price is not None else None,
        price_date=output.price_date,
        formula_version=FORMULA_VERSION,
    )
    db.add(row)
    db.commit()
    return _run_out(row)


@router.get("/companies/{ticker}/valuation/runs", response_model=list[RunSummary])
def list_runs(ticker: str, user: CurrentUser, db: DbSession) -> list[RunSummary]:
    company = resolve_security(db, ticker).company
    rows = db.scalars(
        select(ValuationRun)
        .where(ValuationRun.company_id == company.id, ValuationRun.owner_id == user.id)
        .order_by(ValuationRun.created_at.desc())
        .limit(50)
    )
    return [_summary(r) for r in rows]


def _owned(db: Session, user: User, run_id: uuid.UUID) -> ValuationRun:
    row = db.get(ValuationRun, run_id)
    if row is None or row.owner_id != user.id:
        raise ApiError(404, "valuation_not_found", "No such saved valuation.")
    return row


@router.get("/valuation/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RunOut:
    return _run_out(_owned(db, user, run_id))


@router.delete("/valuation/runs/{run_id}", status_code=204)
def delete_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned(db, user, run_id))
    db.commit()


@router.get("/companies/{ticker}/valuation/relative", response_model=RelativeOut)
def relative(ticker: str, _: CurrentUser, db: DbSession) -> RelativeOut:
    security = resolve_security(db, ticker)
    company: Company = security.company
    peer_basis, industry_basis, peer_ids, rows, price = relative_valuation(db, company)
    peer_tickers = list(
        db.scalars(
            select(Security.ticker)
            .where(Security.company_id.in_(peer_ids), Security.is_active)
            .order_by(Security.ticker)
        )
    )
    prices = load_prices(db, company)
    return RelativeOut(
        ticker=security.ticker,
        peer_basis=peer_basis,
        industry_basis=industry_basis,
        peers=peer_tickers,
        multiples=rows,
        price=price,
        price_date=prices[-1].trade_date if prices else None,
        note=(
            "Multiples come from each company's latest stored snapshot and need stored prices. "
            "Medians use positive multiples only; implied values apply the peer median to this "
            "company's latest figure."
        ),
    )
