"""U.S. Treasury daily par yield curve (home.treasury.gov). Public data; robots.txt allows all.

Yields are published in percent for business days only; the latest row is the most recent
business day. https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "us_treasury"
LICENSE_NOTE = "U.S. Department of the Treasury daily par yield curve rates; public domain."
CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)


@dataclass(frozen=True)
class YieldObservation:
    observed_on: date
    tenor: str  # "10 Yr", "3 Mo", …
    value: float  # decimal, e.g. 0.0529 for 5.29%


def parse_yield_curve(text: str) -> tuple[list[YieldObservation], int]:
    observations: list[YieldObservation] = []
    rejected = 0
    for row in csv.DictReader(io.StringIO(text)):
        try:
            day = datetime.strptime(row.get("Date", ""), "%m/%d/%Y").date()
        except ValueError:
            rejected += 1
            continue
        for tenor, raw in row.items():
            if tenor == "Date" or not raw:
                continue
            try:
                percent = float(raw)
            except ValueError:
                rejected += 1
                continue
            if -5 < percent < 30:
                observations.append(YieldObservation(day, tenor.strip(), percent / 100))
            else:
                rejected += 1
    return observations, rejected


class TreasuryClient:
    def __init__(self, http: httpx.Client, user_agent: str) -> None:
        self._http = http
        self._user_agent = user_agent

    def yield_curve(self, year: int) -> FetchResult[list[YieldObservation]]:
        url = CSV_URL.format(year=year)
        meta = FetchMeta(PROVIDER, "daily_par_yield_curve", url, LICENSE_NOTE, subject=str(year))
        try:
            response = self._http.get(url, headers={"User-Agent": self._user_agent})
        except httpx.HTTPError as exc:
            meta.finish(None, None)
            raise ProviderError(
                "provider_unavailable",
                f"Treasury request failed: {type(exc).__name__}",
                retryable=True,
                meta=meta,
            ) from exc
        meta.finish(response.status_code, response.content)
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unavailable",
                f"Treasury returned HTTP {response.status_code}.",
                http_status=response.status_code,
                retryable=True,
                meta=meta,
            )
        observations, rejected = parse_yield_curve(response.text)
        if not observations:
            raise ProviderError(
                "provider_bad_response", "Treasury yield file had no rates.", meta=meta
            )
        return FetchResult(observations, meta, rejected)


def build_treasury_client(contact: str | None) -> TreasuryClient:
    agent = f"Pinero research ({contact})" if contact else "Pinero research"
    return TreasuryClient(httpx.Client(timeout=20.0, follow_redirects=True), agent)
