"""Official SEC EDGAR adapter.

SEC fair-access policy: at most 10 requests per second and a User-Agent that
identifies the requester with contact details. See
https://www.sec.gov/os/accessing-edgar-data. EDGAR data is public; no API key exists.
"""

import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "sec_edgar"
LICENSE_NOTE = "U.S. SEC EDGAR public data; subject to SEC fair-access policy (<=10 req/s)."
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"
_MIN_INTERVAL_SECONDS = 0.125

ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
# A single file name inside a filing folder; no path separators.
DOCUMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
SUBMISSION_PAGE = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")


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


@dataclass(frozen=True)
class FactObservation:
    """A distinct XBRL value, dated by the earliest filing that made it public."""

    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed_date: date


@dataclass(frozen=True)
class FilingRecord:
    """One row of a company's EDGAR filing index."""

    accession: str
    form: str
    filed_date: date
    accepted_at: datetime | None
    report_date: date | None
    primary_document: str | None
    description: str | None
    # 8-K item numbers, e.g. "2.02,9.01".
    items: str | None
    size: int | None
    is_xbrl: bool
    is_inline_xbrl: bool


@dataclass(frozen=True)
class Submissions:
    profile: SubmissionProfile
    filings: list[FilingRecord]
    # Names of older filing pages (CIK##########-submissions-001.json, …).
    older_pages: list[str]


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

    def _fetch(self, meta: FetchMeta) -> httpx.Response:
        """GET with throttling and retries; raises ProviderError for any non-2xx outcome."""
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
        return response

    def _get_json(self, meta: FetchMeta) -> Any:
        response = self._fetch(meta)
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

    def fetch_submissions(self, cik: int) -> FetchResult[Submissions]:
        """Company profile plus its most recent filings (SEC lists up to about 1,000)."""
        url = SUBMISSIONS_URL.format(cik=cik)
        meta = FetchMeta(PROVIDER, "submissions", url, LICENSE_NOTE, subject=f"CIK{cik:010d}")
        payload = self._get_json(meta)
        if not isinstance(payload, dict) or "cik" not in payload:
            raise ProviderError(
                "provider_bad_response", "Unexpected SEC submissions shape.", meta=meta
            )
        filings_block = payload.get("filings") or {}
        filings, rejected = parse_filings(filings_block.get("recent") or {})
        older = [
            str(page["name"])
            for page in filings_block.get("files") or []
            if isinstance(page, dict) and page.get("name")
        ]
        return FetchResult(
            Submissions(parse_submission_profile(payload), filings, older), meta, rejected
        )

    def fetch_submission_profile(self, cik: int) -> FetchResult[SubmissionProfile]:
        result = self.fetch_submissions(cik)
        return FetchResult(result.data.profile, result.meta)

    def fetch_submission_page(self, cik: int, name: str) -> FetchResult[list[FilingRecord]]:
        """An older-filings page listed in submissions `filings.files`."""
        if not SUBMISSION_PAGE.fullmatch(name):
            raise ProviderError("provider_bad_request", f"Unexpected submissions page {name!r}.")
        url = f"https://data.sec.gov/submissions/{name}"
        meta = FetchMeta(PROVIDER, "submissions_page", url, LICENSE_NOTE, subject=f"CIK{cik:010d}")
        payload = self._get_json(meta)
        if not isinstance(payload, dict):
            raise ProviderError(
                "provider_bad_response", "Unexpected SEC submissions page shape.", meta=meta
            )
        filings, rejected = parse_filings(payload)
        return FetchResult(filings, meta, rejected)

    def fetch_filing_document(self, cik: int, accession: str, document: str) -> FetchResult[bytes]:
        """A document from a filing's archive folder (primary document, Form 4 XML, …)."""
        if not ACCESSION.fullmatch(accession) or not DOCUMENT_NAME.fullmatch(document):
            raise ProviderError(
                "provider_bad_request", "Invalid accession number or document name."
            )
        url = ARCHIVE_URL.format(cik=cik, folder=accession.replace("-", ""), document=document)
        meta = FetchMeta(PROVIDER, "filing_document", url, LICENSE_NOTE, subject=accession)
        response = self._fetch(meta)
        return FetchResult(response.content, meta)

    def fetch_company_facts(
        self,
        cik: int,
        tracked: dict[tuple[str, str], str],
        forms: frozenset[str],
    ) -> FetchResult[list[FactObservation]]:
        """Fetch XBRL company facts, keeping only `tracked` (taxonomy, concept) -> unit."""

        url = COMPANY_FACTS_URL.format(cik=cik)
        meta = FetchMeta(PROVIDER, "companyfacts", url, LICENSE_NOTE, subject=f"CIK{cik:010d}")
        payload = self._get_json(meta)
        if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
            raise ProviderError(
                "provider_bad_response", "Unexpected SEC companyfacts shape.", meta=meta
            )
        observations, rejected = parse_company_facts(payload, tracked, forms)
        return FetchResult(observations, meta, rejected)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def parse_company_facts(
    payload: dict[str, Any],
    tracked: dict[tuple[str, str], str],
    forms: frozenset[str],
) -> tuple[list[FactObservation], int]:
    """Flatten companyfacts into distinct observations.

    The same value is repeated in every later filing that shows it as a comparative;
    only the earliest filing is kept, because that is when it became public. A changed
    value for the same period (a restatement) is kept as a separate observation.
    """

    earliest: dict[tuple[str, str, str, date | None, date, Decimal], FactObservation] = {}
    rejected = 0
    for (taxonomy, concept), unit in tracked.items():
        concept_data = (payload["facts"].get(taxonomy) or {}).get(concept) or {}
        for entry in (concept_data.get("units") or {}).get(unit) or []:
            form = str(entry.get("form") or "")
            if form not in forms:
                continue
            end = _parse_date(entry.get("end"))
            filed = _parse_date(entry.get("filed"))
            start = _parse_date(entry.get("start")) if entry.get("start") else None
            accession = str(entry.get("accn") or "")
            try:
                value = Decimal(str(entry.get("val")))
            except (InvalidOperation, ValueError):
                value = Decimal("NaN")
            if (
                end is None
                or filed is None
                or not accession
                or not value.is_finite()
                or (start is not None and start > end)
                or filed < end
            ):
                rejected += 1
                continue
            fy = entry.get("fy")
            observation = FactObservation(
                taxonomy=taxonomy,
                concept=concept,
                unit=unit,
                period_start=start,
                period_end=end,
                value=value,
                fiscal_year=int(fy) if isinstance(fy, int) else None,
                fiscal_period=str(entry["fp"])[:4] if entry.get("fp") else None,
                form=form[:12],
                accession=accession[:25],
                filed_date=filed,
            )
            key = (taxonomy, concept, unit, start, end, value)
            current = earliest.get(key)
            if current is None or (filed, accession) < (current.filed_date, current.accession):
                earliest[key] = observation
    return list(earliest.values()), rejected


def _parse_accepted(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def parse_filings(columns: dict[str, Any]) -> tuple[list[FilingRecord], int]:
    """Rows from SEC's columnar filing index (`filings.recent` or an older page)."""

    accessions = columns.get("accessionNumber") or []

    def column(name: str, index: int) -> Any:
        values = columns.get(name) or []
        return values[index] if index < len(values) else None

    filings: list[FilingRecord] = []
    rejected = 0
    for index, accession in enumerate(accessions):
        form = _clean(column("form", index), 20)
        filed = _parse_date(column("filingDate", index))
        if not isinstance(accession, str) or not ACCESSION.fullmatch(accession) or not form:
            rejected += 1
            continue
        if filed is None:
            rejected += 1
            continue
        size = column("size", index)
        filings.append(
            FilingRecord(
                accession=accession,
                form=form,
                filed_date=filed,
                accepted_at=_parse_accepted(column("acceptanceDateTime", index)),
                report_date=_parse_date(column("reportDate", index)),
                primary_document=_clean(column("primaryDocument", index), 300),
                description=_clean(column("primaryDocDescription", index), 300),
                items=_clean(column("items", index), 120),
                size=int(size) if isinstance(size, int) else None,
                is_xbrl=bool(column("isXBRL", index)),
                is_inline_xbrl=bool(column("isInlineXBRL", index)),
            )
        )
    return filings, rejected


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
