import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentUser, DbSession
from app.backtesting.schemas import BacktestIn, BacktestOut, BacktestSummary, OptionsOut
from app.backtesting.service import options, run_backtest
from app.core.errors import ApiError
from app.db.models import Backtest, User

router = APIRouter(tags=["backtests"])


@router.get("/backtests/options", response_model=OptionsOut)
def backtest_options(_: CurrentUser, db: DbSession) -> OptionsOut:
    return OptionsOut(**options(db))


@router.post("/backtests", response_model=BacktestOut, status_code=201)
def create_backtest(body: BacktestIn, user: CurrentUser, db: DbSession) -> BacktestOut:
    """Run a factor-rule backtest now (about 10 seconds) and save it with its inputs."""
    return _out(run_backtest(db, body, user.id))


@router.get("/backtests", response_model=list[BacktestSummary])
def list_backtests(user: CurrentUser, db: DbSession) -> list[BacktestSummary]:
    rows = db.scalars(
        select(Backtest)
        .where(Backtest.owner_id == user.id)
        .order_by(Backtest.created_at.desc())
        .limit(100)
    )
    return [
        BacktestSummary(
            id=b.id,
            name=b.name,
            status=b.status,
            created_at=b.created_at,
            summary=b.summary,
            spec=b.spec,
        )
        for b in rows
    ]


def _owned(db: Session, user: User, backtest_id: uuid.UUID) -> Backtest:
    row = db.get(Backtest, backtest_id)
    if row is None or row.owner_id != user.id:
        raise ApiError(404, "backtest_not_found", "No such backtest.")
    return row


@router.get("/backtests/{backtest_id}", response_model=BacktestOut)
def get_backtest(backtest_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BacktestOut:
    return _out(_owned(db, user, backtest_id))


@router.delete("/backtests/{backtest_id}", status_code=204)
def delete_backtest(backtest_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned(db, user, backtest_id))
    db.commit()


def _out(b: Backtest) -> BacktestOut:
    return BacktestOut(
        id=b.id,
        name=b.name,
        status=b.status,
        created_at=b.created_at,
        summary=b.summary,
        spec=b.spec,
        results=b.results,
        error=b.error,
        code_version=b.code_version,
        data_through=b.data_through,
        duration_ms=b.duration_ms,
    )
