from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import AppSettings, CurrentUser, DbSession
from app.companies.schemas import CompanyProfile, CompanySearchResponse
from app.companies.service import (
    build_company_profile,
    refresh_company_profile,
    resolve_security,
    search_companies,
)
from app.core.config import get_settings
from app.providers.sec_edgar import SecEdgarClient, build_sec_client

router = APIRouter(prefix="/companies", tags=["companies"])


@lru_cache
def get_sec_client() -> SecEdgarClient:
    settings = get_settings()
    return build_sec_client(settings.sec_user_agent, settings.sec_timeout_seconds)


SecClient = Annotated[SecEdgarClient, Depends(get_sec_client)]


@router.get("", response_model=CompanySearchResponse)
def search(
    _: CurrentUser,
    db: DbSession,
    query: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> CompanySearchResponse:
    return search_companies(db, query, limit)


@router.get("/{ticker}", response_model=CompanyProfile)
def company_profile(
    ticker: str, _: CurrentUser, db: DbSession, sec: SecClient, settings: AppSettings
) -> CompanyProfile:
    security = resolve_security(db, ticker)
    status, message = refresh_company_profile(db, security.company, sec, settings)
    return build_company_profile(db, security, status, message, settings)
