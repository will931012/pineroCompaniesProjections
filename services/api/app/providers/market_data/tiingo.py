"""Tiingo end-of-day adapter. Requires TIINGO_API_KEY (https://www.tiingo.com/account/api/token)."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError
from app.providers.market_data.base import DailyBar, ProviderInfo, bar_problems

INFO = ProviderInfo(
    name="tiingo",
    display_name="Tiingo",
    license_note=(
        "Tiingo end-of-day data; use is governed by Tiingo's terms and your subscription. "
        "Redistribution may require a commercial license."
    ),
    credential_env="TIINGO_API_KEY",
)
BASE_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _required_decimal(row: dict[str, Any], key: str) -> Decimal:
    value = _decimal(row.get(key))
    if value is None:
        raise ValueError(key)
    return value


def parse_bar(row: dict[str, Any]) -> DailyBar:
    trade_date = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).date()
    adj_volume = row.get("adjVolume")
    return DailyBar(
        trade_date=trade_date,
        open=_required_decimal(row, "open"),
        high=_required_decimal(row, "high"),
        low=_required_decimal(row, "low"),
        close=_required_decimal(row, "close"),
        volume=int(row["volume"]),
        adj_open=_decimal(row.get("adjOpen")),
        adj_high=_decimal(row.get("adjHigh")),
        adj_low=_decimal(row.get("adjLow")),
        adj_close=_decimal(row.get("adjClose")),
        adj_volume=int(adj_volume) if adj_volume is not None else None,
        dividend_cash=_decimal(row.get("divCash")),
        split_factor=_decimal(row.get("splitFactor")),
    )


class TiingoMarketDataProvider:
    info = INFO

    def __init__(self, api_key: str, http: httpx.Client) -> None:
        self._api_key = api_key
        self._http = http

    def get_daily_bars(self, ticker: str, start: date, end: date) -> FetchResult[list[DailyBar]]:
        url = BASE_URL.format(ticker=ticker.lower())
        params = {"startDate": start.isoformat(), "endDate": end.isoformat(), "format": "json"}
        meta = FetchMeta(
            INFO.name,
            "daily_prices",
            f"{url}?startDate={params['startDate']}&endDate={params['endDate']}",
            INFO.license_note,
            subject=ticker,
            request_params={"start": start.isoformat(), "end": end.isoformat()},
        )
        try:
            response = self._http.get(
                url, params=params, headers={"Authorization": f"Token {self._api_key}"}
            )
        except httpx.HTTPError as exc:
            meta.finish(None, None)
            raise ProviderError(
                "provider_unavailable",
                f"Tiingo request failed: {type(exc).__name__}",
                retryable=True,
                meta=meta,
            ) from exc
        meta.finish(response.status_code, response.content)

        if response.status_code in {401, 403}:
            raise ProviderError(
                "provider_auth_failed",
                "Tiingo rejected the API key; check TIINGO_API_KEY.",
                http_status=response.status_code,
                meta=meta,
            )
        if response.status_code == 404:
            raise ProviderError(
                "provider_symbol_not_found",
                f"Tiingo has no end-of-day data for {ticker}.",
                http_status=404,
                meta=meta,
            )
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited",
                "Tiingo rate limit reached.",
                http_status=429,
                retryable=True,
                meta=meta,
            )
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unavailable",
                f"Tiingo returned HTTP {response.status_code}.",
                http_status=response.status_code,
                retryable=True,
                meta=meta,
            )
        try:
            rows = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response", "Tiingo returned invalid JSON.", meta=meta
            ) from exc
        if not isinstance(rows, list):
            raise ProviderError(
                "provider_bad_response", "Unexpected Tiingo response shape.", meta=meta
            )

        bars: list[DailyBar] = []
        rejected = 0
        for row in rows:
            try:
                bar = parse_bar(row)
            except (KeyError, TypeError, ValueError):
                rejected += 1
                continue
            if bar_problems(bar) or not start <= bar.trade_date <= end:
                rejected += 1
                continue
            bars.append(bar)
        return FetchResult(bars, meta, rejected)
