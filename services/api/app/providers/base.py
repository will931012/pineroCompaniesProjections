import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.metrics import PROVIDER_REQUEST_DURATION, PROVIDER_REQUESTS
from app.db.base import utcnow
from app.db.models import ProviderFetch

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A provider could not supply data. `code` is stable and safe to show to clients."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        meta: "FetchMeta | None" = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.meta = meta


@dataclass
class FetchMeta:
    """Provenance for one provider request. Never includes credentials."""

    provider: str
    dataset: str
    source_url: str
    license_note: str
    subject: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=utcnow)
    retrieved_at: datetime | None = None
    latency_ms: int = 0
    http_status: int | None = None
    content_sha256: str | None = None
    _clock: float = field(default_factory=time.perf_counter, repr=False)

    def finish(self, http_status: int | None, content: bytes | None) -> None:
        self.retrieved_at = utcnow()
        self.latency_ms = int((time.perf_counter() - self._clock) * 1000)
        self.http_status = http_status
        if content is not None:
            self.content_sha256 = hashlib.sha256(content).hexdigest()
        PROVIDER_REQUEST_DURATION.labels(self.provider, self.dataset).observe(
            self.latency_ms / 1000
        )


@dataclass
class FetchResult[T]:
    data: T
    meta: FetchMeta
    rejected_count: int = 0


def record_fetch(
    db: Session,
    meta: FetchMeta,
    *,
    status: str,
    record_count: int | None = None,
    rejected_count: int | None = None,
    error: ProviderError | None = None,
) -> ProviderFetch:
    """Persist provenance for a provider request in the caller's transaction."""

    if meta.retrieved_at is None:
        meta.finish(error.http_status if error else None, None)
    PROVIDER_REQUESTS.labels(meta.provider, meta.dataset, status).inc()
    fetch = ProviderFetch(
        provider=meta.provider,
        dataset=meta.dataset,
        subject=meta.subject,
        source_url=meta.source_url[:500],
        request_params=meta.request_params,
        status=status,
        http_status=meta.http_status,
        error_code=error.code if error else None,
        error_message=error.message[:500] if error else None,
        record_count=record_count,
        rejected_count=rejected_count,
        content_sha256=meta.content_sha256,
        license_note=meta.license_note,
        started_at=meta.started_at,
        retrieved_at=meta.retrieved_at,
        latency_ms=meta.latency_ms,
    )
    db.add(fetch)
    db.flush()
    if error is not None:
        logger.warning(
            "provider_fetch_failed",
            extra={
                "provider": meta.provider,
                "dataset": meta.dataset,
                "error_code": error.code,
                "http_status": meta.http_status,
            },
        )
    return fetch
