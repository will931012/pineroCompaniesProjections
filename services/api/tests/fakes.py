"""Deterministic HTTP fakes for provider adapters.

These payloads mirror the providers' documented response shapes. They are test
fixtures only and are never served by the application.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.market_data.tiingo import TiingoMarketDataProvider
from app.providers.sec_edgar import SecEdgarClient

TEST_USER_AGENT = "Pinero Test Suite tests@example.com"
TEST_TIINGO_KEY = "test-key-not-real"


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode())


def directory_payload(rows: list[list[object]]) -> dict[str, Any]:
    return {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}


def submissions_payload(cik: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "cik": str(cik),
        "entityType": "operating",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "name": "Example Devices Inc.",
        "tickers": ["EXDV"],
        "exchanges": ["Nasdaq"],
        "category": "Large accelerated filer",
        "fiscalYearEnd": "0926",
        "stateOfIncorporation": "CA",
        "stateOfIncorporationDescription": "CA",
        "website": "",
        "description": "",
        "addresses": {
            "business": {
                "city": "CUPERTINO",
                "stateOrCountry": "CA",
                "stateOrCountryDescription": "CA",
                "isForeignLocation": 0,
            }
        },
        "formerNames": [{"name": "Example Computer Inc", "from": "1994-01-01", "to": "2007-01-01"}],
    }
    payload.update(overrides)
    return payload


def sec_client_with(
    directory_rows: list[list[object]] | None = None,
    submissions: dict[int, dict[str, Any]] | None = None,
    facts: dict[int, dict[str, Any]] | None = None,
    status: int = 200,
    user_agent: str | None = TEST_USER_AGENT,
    calls: list[httpx.Request] | None = None,
    documents: dict[str, bytes] | None = None,
    pages: dict[str, dict[str, Any]] | None = None,
) -> SecEdgarClient:
    """`documents` maps archive paths (/Archives/edgar/data/…) to bytes; `pages` maps older
    submissions page names to their payloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        assert request.headers["User-Agent"] == user_agent
        if status != 200:
            return httpx.Response(status)
        path = request.url.path
        if path.startswith("/Archives/"):
            content = (documents or {}).get(path)
            return (
                httpx.Response(200, content=content) if content is not None else httpx.Response(404)
            )
        if "-submissions-" in path:
            page = (pages or {}).get(path.rsplit("/", 1)[1])
            return json_response(page) if page is not None else httpx.Response(404)
        if path.endswith("company_tickers_exchange.json"):
            return json_response(directory_payload(directory_rows or []))
        cik = int(request.url.path.rsplit("CIK", 1)[1].removesuffix(".json"))
        if "/api/xbrl/companyfacts/" in request.url.path:
            if facts and cik in facts:
                return json_response(facts[cik])
            return httpx.Response(404)
        if submissions and cik in submissions:
            return json_response(submissions[cik])
        return httpx.Response(404)

    client = SecEdgarClient(
        user_agent, httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1
    )
    return client


def tiingo_row(day: str, close: float, **overrides: Any) -> dict[str, Any]:
    row = {
        "date": f"{day}T00:00:00.000Z",
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": 1_000_000,
        "adjOpen": close - 1,
        "adjHigh": close + 2,
        "adjLow": close - 2,
        "adjClose": close,
        "adjVolume": 1_000_000,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }
    row.update(overrides)
    return row


def tiingo_with(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TiingoMarketDataProvider:
    return TiingoMarketDataProvider(
        TEST_TIINGO_KEY, httpx.Client(transport=httpx.MockTransport(handler))
    )
