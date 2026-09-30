from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel

from app.db.models import ProviderFetch


class SourceRef(BaseModel):
    """Where a set of values came from. Every externally sourced field links to one."""

    fetch_id: int
    provider: str
    dataset: str
    source_url: str
    retrieved_at: datetime
    license_note: str | None


def source_refs(fetches: Iterable[ProviderFetch | None]) -> list[SourceRef]:
    return [
        SourceRef(
            fetch_id=f.id,
            provider=f.provider,
            dataset=f.dataset,
            source_url=f.source_url,
            retrieved_at=f.retrieved_at,
            license_note=f.license_note,
        )
        for f in sorted((f for f in fetches if f is not None), key=lambda f: f.id)
    ]
