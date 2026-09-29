from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderFetch(Base):
    """One outbound request to an external data provider.

    Every externally sourced row references the fetch that produced it, which gives
    source, source URL, retrieval time, license terms, latency, and outcome for each value.
    """

    __tablename__ = "provider_fetches"
    __table_args__ = (
        Index("ix_provider_fetches_lookup", "provider", "dataset", "subject", "retrieved_at"),
        Index("ix_provider_fetches_retrieved_at", "retrieved_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    dataset: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str | None] = mapped_column(String(64))
    # Never contains credentials; adapters strip tokens before recording.
    source_url: Mapped[str] = mapped_column(String(500))
    request_params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    record_count: Mapped[int | None] = mapped_column(Integer)
    rejected_count: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    license_note: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int] = mapped_column(Integer)
