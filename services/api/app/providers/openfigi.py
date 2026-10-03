"""OpenFIGI (Bloomberg) identifier mapping: CUSIP → FIGI, ticker, exchange.

Without a key OpenFIGI allows 25 requests a minute with 10 identifiers each; with a free key,
25 requests every 6 seconds with 100 each. On HTTP 429 the client waits for the window in the
`ratelimit-reset` header. https://www.openfigi.com/api/documentation
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "openfigi"
LICENSE_NOTE = "OpenFIGI (Bloomberg) identifier mapping; FIGI is an open standard (OMG)."
MAPPING_URL = "https://api.openfigi.com/v3/mapping"


@dataclass(frozen=True)
class FigiMatch:
    cusip: str
    figi: str | None
    composite_figi: str | None
    ticker: str | None
    exch_code: str | None
    name: str | None
    security_type: str | None


def _us_listing(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the US composite listing (exchCode "US"), then any listing with a ticker."""
    for row in rows:
        if row.get("exchCode") == "US" and row.get("ticker"):
            return row
    for row in rows:
        if row.get("ticker"):
            return row
    return rows[0] if rows else None


def parse_mapping(cusips: list[str], payload: list[Any]) -> list[FigiMatch]:
    matches = []
    for cusip, job in zip(cusips, payload, strict=False):
        row = _us_listing(job.get("data") or []) if isinstance(job, dict) else None
        if row is None:
            matches.append(FigiMatch(cusip, None, None, None, None, None, None))
            continue
        matches.append(
            FigiMatch(
                cusip,
                row.get("figi"),
                row.get("compositeFIGI"),
                row.get("ticker"),
                row.get("exchCode"),
                (row.get("name") or None) and str(row["name"])[:200],
                row.get("securityType"),
            )
        )
    return matches


class OpenFigiClient:
    def __init__(self, api_key: str | None, http: httpx.Client, *, sleep: Any = time.sleep) -> None:
        self._api_key = api_key
        self._http = http
        self._sleep = sleep
        self.batch_size = 100 if api_key else 10
        # Spread requests evenly inside the window instead of bursting into a 429.
        self._interval = 6 / 25 if api_key else 60 / 25
        self._last = 0.0

    def map_cusips(self, cusips: list[str], max_attempts: int = 4) -> FetchResult[list[FigiMatch]]:
        """Map up to `batch_size` CUSIPs in one request."""
        if not cusips or len(cusips) > self.batch_size:
            raise ProviderError("provider_bad_request", f"Send 1–{self.batch_size} CUSIPs.")
        meta = FetchMeta(
            PROVIDER, "mapping", MAPPING_URL, LICENSE_NOTE, subject=f"{len(cusips)} CUSIPs",
            request_params={"cusips": cusips},
        )  # fmt: skip
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-OPENFIGI-APIKEY"] = self._api_key
        body = [{"idType": "ID_CUSIP", "idValue": cusip} for cusip in cusips]
        response: httpx.Response | None = None
        for _attempt in range(max_attempts):
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._http.post(MAPPING_URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                meta.finish(None, None)
                raise ProviderError(
                    "provider_unavailable",
                    f"OpenFIGI request failed: {type(exc).__name__}",
                    retryable=True,
                    meta=meta,
                ) from exc
            if response.status_code != 429:
                break
            reset = response.headers.get("ratelimit-reset")
            self._sleep(float(reset) + 1 if reset and reset.isdigit() else 60)
        assert response is not None
        meta.finish(response.status_code, response.content)
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited", "OpenFIGI rate limit reached.", http_status=429,
                retryable=True, meta=meta,
            )  # fmt: skip
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unavailable",
                f"OpenFIGI returned HTTP {response.status_code}.",
                http_status=response.status_code,
                retryable=response.status_code >= 500,
                meta=meta,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response", "OpenFIGI returned invalid JSON.", meta=meta
            ) from exc
        if not isinstance(payload, list) or len(payload) != len(cusips):
            snippet = " ".join(response.text[:160].split())
            raise ProviderError(
                "provider_bad_response",
                f"OpenFIGI returned a different number of results: {snippet}",
                retryable=True,
                meta=meta,
            )
        return FetchResult(parse_mapping(cusips, payload), meta)


def build_openfigi_client(api_key: str | None) -> OpenFigiClient:
    return OpenFigiClient(api_key, httpx.Client(timeout=30.0))
