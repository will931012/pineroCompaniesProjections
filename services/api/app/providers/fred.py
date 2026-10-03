"""FRED (Federal Reserve Bank of St. Louis) series with their ALFRED vintages.

Observations are requested over every real-time period (realtime_start 1776-07-04 to
9999-12-31), so each value comes with the dates it was the published figure. That is what
lets a feature use only what was known on a past date. The API key is sent as a query
parameter but never stored: provenance records the URL without it.

This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank
of St. Louis. https://fred.stlouisfed.org/docs/api/terms_of_use.html
"""

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "fred"
LICENSE_NOTE = (
    "FRED® API, Federal Reserve Bank of St. Louis; not endorsed or certified by the Bank. "
    "Series data belong to their sources (BLS, BEA, Federal Reserve, Treasury, DOL)."
)
API_ROOT = "https://api.stlouisfed.org/fred"
PAGE_LIMIT = 100_000
# FRED allows 120 requests a minute; one every 0.6 s keeps well inside it.
MIN_INTERVAL_SECONDS = 0.6


@dataclass(frozen=True)
class SeriesInfo:
    series_id: str
    title: str
    units: str
    frequency: str


@dataclass(frozen=True)
class Vintage:
    observation_date: date
    realtime_start: date
    realtime_end: date
    value: float


def _date(value: str) -> date:
    return date.fromisoformat(value)


def parse_observations(payload: dict[str, Any]) -> tuple[list[Vintage], int]:
    """FRED marks missing values with "."; those are skipped, malformed rows are counted."""
    vintages: list[Vintage] = []
    rejected = 0
    for row in payload.get("observations") or []:
        raw = row.get("value")
        if raw == ".":
            continue
        try:
            vintages.append(
                Vintage(
                    _date(row["date"]),
                    _date(row["realtime_start"]),
                    _date(row["realtime_end"]),
                    float(raw),
                )
            )
        except (KeyError, TypeError, ValueError):
            rejected += 1
    return vintages, rejected


class FredClient:
    def __init__(self, api_key: str | None, http: httpx.Client) -> None:
        self._api_key = api_key
        self._http = http
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _get(self, meta: FetchMeta, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError(
                "fred_not_configured",
                "FRED_API_KEY is not set. Get a free key at fredaccount.stlouisfed.org.",
                meta=meta,
            )
        wait = self._last + MIN_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            response = self._http.get(
                f"{API_ROOT}/{path}",
                params={**params, "api_key": self._api_key, "file_type": "json"},
            )
        except httpx.HTTPError as exc:
            meta.finish(None, None)
            raise ProviderError(
                "provider_unavailable",
                f"FRED request failed: {type(exc).__name__}",
                retryable=True,
                meta=meta,
            ) from exc
        meta.finish(response.status_code, response.content)
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited", "FRED rate limit reached.", http_status=429,
                retryable=True, meta=meta,
            )  # fmt: skip
        if response.status_code >= 400:
            # FRED reports a bad key or unknown series as 400 with an error_message.
            try:
                detail = response.json().get("error_message", "")
            except ValueError:
                detail = ""
            raise ProviderError(
                "provider_bad_request" if response.status_code == 400 else "provider_unavailable",
                f"FRED returned HTTP {response.status_code}. {detail}".strip(),
                http_status=response.status_code,
                retryable=response.status_code >= 500,
                meta=meta,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response", "FRED returned invalid JSON.", meta=meta
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError("provider_bad_response", "Unexpected FRED response.", meta=meta)
        return payload

    def series_info(self, series_id: str) -> FetchResult[SeriesInfo]:
        meta = FetchMeta(
            PROVIDER, "series", f"{API_ROOT}/series?series_id={series_id}", LICENSE_NOTE,
            subject=series_id,
        )  # fmt: skip
        payload = self._get(meta, "series", {"series_id": series_id})
        rows = payload.get("seriess") or []
        if not rows:
            raise ProviderError("provider_record_not_found", f"FRED has no series {series_id}.")
        row = rows[0]
        return FetchResult(
            SeriesInfo(
                series_id,
                str(row.get("title", series_id))[:300],
                str(row.get("units", ""))[:120],
                str(row.get("frequency", ""))[:40],
            ),
            meta,
        )

    def vintages(
        self, series_id: str, observation_start: date, *, all_vintages: bool = True
    ) -> FetchResult[list[Vintage]]:
        """Every published value since `observation_start`, with its real-time period.

        FRED serves at most 2,000 vintage dates per request, which daily series exceed; for
        those (`all_vintages=False`) only the current values are requested."""
        base: dict[str, Any] = {
            "series_id": series_id,
            "observation_start": observation_start.isoformat(),
            "limit": PAGE_LIMIT,
        }
        url = (
            f"{API_ROOT}/series/observations?series_id={series_id}"
            f"&observation_start={observation_start.isoformat()}"
        )
        if all_vintages:
            base |= {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}
            url += "&realtime_start=1776-07-04&realtime_end=9999-12-31"
        collected: list[Vintage] = []
        rejected = 0
        offset = 0
        while True:
            meta = FetchMeta(
                PROVIDER, "series_observations", url, LICENSE_NOTE, subject=series_id,
                request_params={"offset": offset, "all_vintages": all_vintages},
            )  # fmt: skip
            payload = self._get(meta, "series/observations", {**base, "offset": offset})
            page, bad = parse_observations(payload)
            collected.extend(page)
            rejected += bad
            count = int(payload.get("count") or 0)
            offset += PAGE_LIMIT
            if offset >= count:
                break
        return FetchResult(collected, meta, rejected)


def build_fred_client(api_key: str | None) -> FredClient:
    return FredClient(api_key, httpx.Client(timeout=60.0))
