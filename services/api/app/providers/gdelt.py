"""GDELT DOC 2.0 API adapter (article search across worldwide online news).

GDELT data is free for any use with attribution to the GDELT Project. The API asks for at
most one request every five seconds; this client waits ten seconds between requests in the
process and backs off on HTTP 429. Only article metadata is returned (headline, outlet, link,
time); article text is never fetched from the outlets.
https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.base import FetchMeta, FetchResult, ProviderError

PROVIDER = "gdelt"
DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
LICENSE_NOTE = (
    "GDELT Project open data; free for any use with attribution (gdeltproject.org). "
    "Headlines and links only; articles belong to their publishers."
)
# GDELT asks for one request per 5 s; in practice bursts near that pace get HTTP 429.
MIN_INTERVAL_SECONDS = 10.0
RATE_LIMIT_BACKOFF_SECONDS = 20.0

_SPACE_BEFORE = re.compile(r"\s+([,.:;!?%)\]])")
_SPACE_AFTER = re.compile(r"([(\[$])\s+")
_DIGIT_GROUPS = re.compile(r"(\d)\s*,\s*(?=\d{3}\b)")
_HYPHEN = re.compile(r"(\w)\s+-\s+(\w)")
_DECIMAL = re.compile(r"(\d)\s*\.\s+(\d)")
_APOSTROPHE = re.compile(r"(\w)\s+'\s*(s|t|re|ve|ll|d|m)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GdeltArticle:
    url: str
    title: str
    domain: str | None
    language: str | None
    source_country: str | None
    seen_at: datetime


def normalise_title(title: str) -> str:
    """GDELT tokenises headlines ("Apple ( NASDAQ : AAPL ) … $1 , 999"); undo the spacing."""
    text = " ".join(title.split())
    text = _DIGIT_GROUPS.sub(r"\1,", text)
    text = _DECIMAL.sub(r"\1.\2", text)
    text = _SPACE_BEFORE.sub(r"\1", text)
    text = _SPACE_AFTER.sub(r"\1", text)
    text = _HYPHEN.sub(r"\1-\2", text)
    text = _APOSTROPHE.sub(r"\1'\2", text)
    return text.replace(" : ", ": ").strip()


def _seen(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def parse_articles(payload: Any) -> tuple[list[GdeltArticle], int]:
    if not isinstance(payload, dict):
        return [], 0
    articles: list[GdeltArticle] = []
    rejected = 0
    for row in payload.get("articles") or []:
        url = str(row.get("url") or "") if isinstance(row, dict) else ""
        title = normalise_title(str(row.get("title") or "")) if isinstance(row, dict) else ""
        seen = _seen(row.get("seendate")) if isinstance(row, dict) else None
        if not url.startswith(("http://", "https://")) or not title or seen is None:
            rejected += 1
            continue
        articles.append(
            GdeltArticle(
                url=url[:2000],
                title=title[:500],
                domain=(str(row.get("domain") or "")[:200] or None),
                language=(str(row.get("language") or "")[:40] or None),
                source_country=(str(row.get("sourcecountry") or "")[:80] or None),
                seen_at=seen,
            )
        )
    return articles, rejected


class GdeltClient:
    _lock = threading.Lock()
    _last_request = 0.0

    def __init__(
        self,
        http: httpx.Client,
        user_agent: str,
        max_attempts: int = 3,
        min_interval: float = MIN_INTERVAL_SECONDS,
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._max_attempts = max_attempts
        self._min_interval = min_interval

    def _throttle(self) -> None:
        with GdeltClient._lock:
            wait = GdeltClient._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            GdeltClient._last_request = time.monotonic()

    def search(
        self, query: str, *, timespan: str = "7d", max_records: int = 75
    ) -> FetchResult[list[GdeltArticle]]:
        params = {
            "query": f"{query} sourcelang:english",
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_records),
            "sort": "DateDesc",
            "timespan": timespan,
        }
        url = str(httpx.URL(DOC_API, params=params))
        meta = FetchMeta(PROVIDER, "doc_artlist", url, LICENSE_NOTE, subject=query[:120])
        response: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                response = self._http.get(url, headers={"User-Agent": self._user_agent})
            except httpx.HTTPError as exc:
                if attempt == self._max_attempts:
                    meta.finish(None, None)
                    raise ProviderError(
                        "provider_unavailable",
                        f"GDELT request failed: {type(exc).__name__}",
                        retryable=True,
                        meta=meta,
                    ) from exc
                continue
            if response.status_code in {429, 500, 502, 503} and attempt < self._max_attempts:
                pause = RATE_LIMIT_BACKOFF_SECONDS if response.status_code == 429 else 2.0
                time.sleep(min(pause, self._min_interval * 2) * attempt)
                continue
            break
        assert response is not None
        meta.finish(response.status_code, response.content)
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited",
                "GDELT asked to slow down (at most one request every 5 seconds).",
                http_status=429,
                retryable=True,
                meta=meta,
            )
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unavailable",
                f"GDELT returned HTTP {response.status_code}.",
                http_status=response.status_code,
                retryable=True,
                meta=meta,
            )
        if not response.content.strip():
            return FetchResult([], meta)  # GDELT answers an empty body when nothing matches
        try:
            payload = response.json()
        except ValueError as exc:
            # GDELT reports query errors ("phrase too short", …) as plain text.
            raise ProviderError(
                "provider_bad_request", response.text.strip()[:300], meta=meta
            ) from exc
        articles, rejected = parse_articles(payload)
        return FetchResult(articles, meta, rejected)


def build_gdelt_client(contact: str | None, timeout: float = 30.0) -> GdeltClient:
    agent = f"Pinero research ({contact})" if contact else "Pinero research"
    return GdeltClient(httpx.Client(timeout=timeout, follow_redirects=True), agent)
