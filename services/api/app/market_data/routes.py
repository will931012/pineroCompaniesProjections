from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import AppSettings, CurrentUser, DbSession
from app.market_data.schemas import MarketBarsResponse
from app.market_data.service import get_daily_bars
from app.providers.market_data.base import MarketDataProvider
from app.providers.market_data.registry import get_market_data_provider

router = APIRouter(prefix="/market-data", tags=["market-data"])


def configured_provider() -> MarketDataProvider | None:
    return get_market_data_provider()


Provider = Annotated[MarketDataProvider | None, Depends(configured_provider)]


@router.get("/{ticker}/bars", response_model=MarketBarsResponse)
def daily_bars(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    provider: Provider,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    interval: Literal["1d"] = "1d",
) -> MarketBarsResponse:
    del interval
    return get_daily_bars(db, provider, settings, ticker, date_from, date_to)
