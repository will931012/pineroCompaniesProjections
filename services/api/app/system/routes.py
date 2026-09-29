from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import AppSettings, CurrentUser, DbSession
from app.db.models import Company, ProviderFetch, Security
from app.db.session import get_engine
from app.providers.market_data.registry import market_data_status
from app.providers.sec_edgar import PROVIDER as SEC_PROVIDER

health_router = APIRouter(tags=["health"])
router = APIRouter(prefix="/system", tags=["system"])


@health_router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
def ready(settings: AppSettings) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        checks["database"] = "unavailable"
    if settings.rate_limit_backend == "redis":
        try:
            Redis.from_url(
                settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5
            ).ping()
            checks["redis"] = "ok"
        except RedisError:
            # Rate limiting degrades to per-process memory, so Redis is not required.
            checks["redis"] = "degraded"
    ready_ = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if ready_ else 503,
        content={"status": "ready" if ready_ else "unavailable", "checks": checks},
    )


class ProviderHealth(BaseModel):
    key: str
    name: str | None
    kind: Literal["reference", "market_data"]
    configured: bool
    message: str
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_code: str | None


class DirectoryStats(BaseModel):
    companies: int
    active_securities: int
    last_synced_at: datetime | None


class SystemStatus(BaseModel):
    environment: str
    providers: list[ProviderHealth]
    directory: DirectoryStats


def _last(db: DbSession, provider: str, dataset: str | None, status: str) -> ProviderFetch | None:
    query = select(ProviderFetch).where(
        ProviderFetch.provider == provider, ProviderFetch.status == status
    )
    if dataset:
        query = query.where(ProviderFetch.dataset == dataset)
    return db.scalar(query.order_by(ProviderFetch.id.desc()).limit(1))


@router.get("/status", response_model=SystemStatus)
def status(_: CurrentUser, db: DbSession, settings: AppSettings) -> SystemStatus:
    sec_ok = _last(db, SEC_PROVIDER, None, "success")
    sec_err = _last(db, SEC_PROVIDER, None, "error")
    directory_ok = _last(db, SEC_PROVIDER, "company_tickers_exchange", "success")
    market = market_data_status(settings)
    market_ok = _last(db, market.name, None, "success") if market.name else None
    market_err = _last(db, market.name, None, "error") if market.name else None
    return SystemStatus(
        environment=settings.app_env,
        providers=[
            ProviderHealth(
                key="sec_edgar",
                name="SEC EDGAR",
                kind="reference",
                configured=bool(settings.sec_user_agent),
                message="Configured."
                if settings.sec_user_agent
                else "Set SEC_USER_AGENT to 'Organization contact@example.com'.",
                last_success_at=sec_ok.retrieved_at if sec_ok else None,
                last_error_at=sec_err.retrieved_at if sec_err else None,
                last_error_code=sec_err.error_code if sec_err else None,
            ),
            ProviderHealth(
                key="market_data",
                name=market.name,
                kind="market_data",
                configured=market.configured,
                message=market.message,
                last_success_at=market_ok.retrieved_at if market_ok else None,
                last_error_at=market_err.retrieved_at if market_err else None,
                last_error_code=market_err.error_code if market_err else None,
            ),
        ],
        directory=DirectoryStats(
            companies=db.scalar(select(func.count()).select_from(Company)) or 0,
            active_securities=db.scalar(
                select(func.count()).select_from(Security).where(Security.is_active)
            )
            or 0,
            last_synced_at=directory_ok.retrieved_at if directory_ok else None,
        ),
    )
