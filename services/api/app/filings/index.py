"""Store rows of a company's EDGAR filing index. Existing rows are left untouched."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Company, Filing
from app.providers.sec_edgar import FilingRecord

_CHUNK = 1000


def store_filing_index(
    db: Session, company: Company, filings: Sequence[FilingRecord], fetch_id: int
) -> int:
    """Insert filings not yet indexed; returns how many were new."""
    before = db.scalar(select(func.count()).where(Filing.company_id == company.id)) or 0
    rows = [
        {
            "company_id": company.id,
            "accession": f.accession,
            "form": f.form,
            "filed_date": f.filed_date,
            "accepted_at": f.accepted_at,
            "report_date": f.report_date,
            "primary_document": f.primary_document,
            "description": f.description,
            "items": f.items,
            "size": f.size,
            "is_xbrl": f.is_xbrl,
            "is_inline_xbrl": f.is_inline_xbrl,
            "index_fetch_id": fetch_id,
        }
        for f in filings
    ]
    for start in range(0, len(rows), _CHUNK):
        db.execute(insert(Filing).values(rows[start : start + _CHUNK]).on_conflict_do_nothing())
    after = db.scalar(select(func.count()).where(Filing.company_id == company.id)) or 0
    return after - before
