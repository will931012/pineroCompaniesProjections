"""Official SEC EDGAR adapter.

SEC fair-access policy: at most 10 requests per second and a User-Agent that
identifies the requester with contact details. See
https://www.sec.gov/os/accessing-edgar-data. EDGAR data is public; no API key exists.
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "sec_edgar"
LICENSE_NOTE = "U.S. SEC EDGAR public data; subject to SEC fair-access policy (<=10 req/s)."
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_MIN_INTERVAL_SECONDS = 0.125


@dataclass(frozen=True)
class DirectoryEntry:
    cik: int
    name: str
    ticker: str
    exchange: str | None


@dataclass(frozen=True)
class SubmissionProfile:
    cik: int
    name: str
    entity_type: str | None
    sic_code: str | None
    sic_description: str | None
    filer_category: str | None
    state_of_incorporation: str | None
    fiscal_year_end: str | None
    website: str | None
    description: str | None
    hq_city: str | None
    hq_region: str | None
    hq_is_foreign: bool | None
    former_names: list[dict[str, Any]]
    tickers: list[str]
    exchanges: list[str | None]


class SecEdgarClient:
    _lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, user_agent: str | None, http: httpx.Client, max_attempts: int = 3) -> None:
        self._user_agent = (user_agent or "").strip()
        self._http = http
        self._max_attempts = max_attempts

    @property
    def configured(self) -> bool:
        return bool(self._user_agent)

    def _throttle(self) -> None:
        with SecEdgarClient._lock:
            wait = SecEdgarClient._last_request + _MIN_INTERVAL_SECONDS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            SecEdgarClient._last_request = time.monotonic()

    def _get_json(self, meta: FetchMeta) -> Any:
        if not self.configured:
            raise ProviderError(
                "sec_user_agent_missing",
                "SEC_USER_AGENT is not set. SEC requires a User-Agent like "
                "'Company Name admin@example.com'.",
                meta=meta,
            )
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
        response: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                response = self._http.get(meta.source_url, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self._max_attempts:
                    meta.finish(None, None)
                    raise ProviderError(
                        "provider_unavailable",
                        f"SEC EDGAR request failed: {type(exc).__name__}",
                        retryable=True,
                        meta=meta,
                    ) from exc
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < self._max_attempts:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            break
        assert response is not None
        meta.finish(response.status_code, response.content)
        if response.status_code == 404:
            raise ProviderError(
                "provider_record_not_found", "SEC EDGAR has no record.", http_status=404, meta=meta
            )
        if response.status_code == 403:
            raise ProviderError(
                "provider_access_denied",
                "SEC EDGAR refused the request; check SEC_USER_AGENT and request rate.",
                http_status=403,
                meta=meta,
            )
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited",
                "SEC EDGAR rate limit reached.",
                http_status=429,
                retryable=True,
                meta=meta,
            )
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unavailable",
                f"SEC EDGAR returned HTTP {response.status_code}.",
                http_status=response.status_code,
                retryable=True,
                meta=meta,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response",
                "SEC EDGAR returned invalid JSON.",
                http_status=response.status_code,
                meta=meta,
            ) from exc

    def fetch_directory(self) -> FetchResult[list[DirectoryEntry]]:
        meta = FetchMeta(PROVIDER, "company_tickers_exchange", TICKERS_URL, LICENSE_NOTE)
        payload = self._get_json(meta)
        try:
            fields = payload["fields"]
            index = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
            rows = payload["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "provider_bad_response", "Unexpected SEC ticker file shape.", meta=meta
            ) from exc

        entries: list[DirectoryEntry] = []
        rejected = 0
        for row in rows:
            try:
                cik = int(row[index["cik"]])
                name = str(row[index["name"]]).strip()
                ticker = str(row[index["ticker"]]).strip().upper()
                exchange = row[index["exchange"]]
            except (IndexError, TypeError, ValueError):
                rejected += 1
                continue
            if cik <= 0 or not name or not ticker or len(ticker) > 20:
                rejected += 1
                continue
            entries.append(
                DirectoryEntry(
                    cik, name[:240], ticker, str(exchange).strip()[:80] if exchange else None
                )
            )
        return FetchResult(entries, meta, rejected)

    def fetch_submission_profile(self, cik: int) -> FetchResult[SubmissionProfile]:
        url = SUBMISSIONS_URL.format(cik=cik)
        meta = FetchMeta(PROVIDER, "submissions", url, LICENSE_NOTE, subject=f"CIK{cik:010d}")
        payload = self._get_json(meta)
        if not isinstance(payload, dict) or "cik" not in payload:
            raise ProviderError(
                "provider_bad_response", "Unexpected SEC submissions shape.", meta=meta
            )
        return FetchResult(parse_submission_profile(payload), meta)


def _clean(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] or None


def parse_submission_profile(payload: dict[str, Any]) -> SubmissionProfile:
    business = (payload.get("addresses") or {}).get("business") or {}
    foreign_flag = business.get("isForeignLocation")
    fiscal_year_end = _clean(payload.get("fiscalYearEnd"), 4)
    sic = _clean(payload.get("sic"), 4)
    return SubmissionProfile(
        cik=int(payload["cik"]),
        name=_clean(payload.get("name"), 240) or "",
        entity_type=_clean(payload.get("entityType"), 40),
        sic_code=sic if sic and sic.isdigit() else None,
        sic_description=_clean(payload.get("sicDescription"), 160),
        filer_category=_clean(payload.get("category"), 80),
        state_of_incorporation=_clean(
            payload.get("stateOfIncorporationDescription") or payload.get("stateOfIncorporation"),
            80,
        ),
        fiscal_year_end=fiscal_year_end if fiscal_year_end and fiscal_year_end.isdigit() else None,
        website=_clean(payload.get("website"), 300),
        description=_clean(payload.get("description"), 5000),
        hq_city=_clean(business.get("city"), 120),
        hq_region=_clean(business.get("stateOrCountryDescription"), 120),
        hq_is_foreign=None if foreign_flag is None else bool(foreign_flag),
        former_names=[
            {"name": item.get("name"), "from": item.get("from"), "to": item.get("to")}
            for item in payload.get("formerNames") or []
            if isinstance(item, dict) and item.get("name")
        ],
        tickers=[str(t).upper() for t in payload.get("tickers") or []],
        exchanges=list(payload.get("exchanges") or []),
    )


def build_sec_client(user_agent: str | None, timeout: float) -> SecEdgarClient:
    return SecEdgarClient(user_agent, httpx.Client(timeout=timeout, follow_redirects=True))
