import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import Select, and_, case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.companies.service import refresh_company_profile
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Company, Filing, FilingChunk, FilingSection, InsiderTransaction
from app.filings.chunks import chunk_section
from app.filings.exhibits import parse_exhibits, press_release
from app.filings.form4 import FORM4_PARSER_VERSION, Form4Error, parse_form4, raw_xml_name
from app.filings.html_text import document_paragraphs
from app.filings.index import store_filing_index
from app.filings.sections import EXTRACTOR_VERSION, Section, extract_sections, form_family
from app.providers.base import ProviderError, record_fetch
from app.providers.embeddings import EmbeddingProvider
from app.providers.sec_edgar import SecEdgarClient

logger = logging.getLogger(__name__)

INSIDER_FORMS = ("4", "4/A")
# Forms whose primary document is split into items and indexed for search by default.
CORE_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A")
_TEXT_DOCUMENTS = (".htm", ".html", ".xhtml", ".txt")
_EXHIBIT_LABEL = re.compile(
    r"^(ex-\d+(\.\d+)?|exhibit \d+(\.\d+)?|[\w.-]+\.(htm|html|txt))\.?$", re.IGNORECASE
)
NOT_EMBEDDED_SECTIONS = ("item_8", "item_15", "part1_item_1", "part2_item_6", "item_9.01")
_EMBED_BATCH = 32


def refresh_filing_index(
    db: Session,
    company: Company,
    client: SecEdgarClient,
    settings: Settings,
    *,
    force: bool = False,
) -> tuple[str, str | None]:
    """Refresh the recent-filings index (and SEC profile) when older than the TTL."""
    if company.cik is None:
        return "not_applicable", "This company has no SEC CIK, so SEC filings are unavailable."
    ttl = timedelta(hours=settings.filings_ttl_hours)
    if (
        not force
        and company.filings_refreshed_at is not None
        and utcnow() - company.filings_refreshed_at < ttl
    ):
        return "current", None
    # The submissions response carries both the profile and the filing index.
    status, message = refresh_company_profile(db, company, client, settings, force=True)
    if status == "current":
        return "current", None
    return ("stale" if company.filings_refreshed_at else status), message


def load_filing_history(db: Session, company: Company, client: SecEdgarClient) -> int:
    """Index the older filing pages beyond SEC's most recent ~1,000 filings."""
    if company.cik is None:
        return 0
    submissions = client.fetch_submissions(company.cik)
    added = 0
    for page in submissions.data.older_pages:
        try:
            result = client.fetch_submission_page(company.cik, page)
        except ProviderError as error:
            if error.meta is not None:
                record_fetch(db, error.meta, status="error", error=error)
                db.commit()
            raise
        fetch = record_fetch(
            db,
            result.meta,
            status="success",
            record_count=len(result.data),
            rejected_count=result.rejected_count,
        )
        added += store_filing_index(db, company, result.data, fetch.id)
    company.filings_history_loaded = True
    db.commit()
    return added


def _fail(db: Session, filing: Filing, status: str, message: str) -> tuple[str, str]:
    filing.document_status = status
    filing.document_error = message[:300]
    db.commit()
    return status, message


def _embeddable() -> Select[FilingChunk, str]:
    """Passages worth embedding: financial statements and exhibits are mostly tables, so
    they stay in full-text search only."""
    return (
        select(FilingChunk, FilingSection.title)
        .join(FilingSection, FilingSection.id == FilingChunk.section_id)
        .where(FilingSection.key.not_in(NOT_EMBEDDED_SECTIONS))
    )


def embedding_counts(db: Session, company_id: int) -> tuple[int, int]:
    """(passages worth embedding, passages embedded) for a company."""
    subquery = _embeddable().where(FilingChunk.company_id == company_id).subquery()
    total, embedded = db.execute(select(func.count(), func.count(subquery.c.embedding))).one()
    return total, embedded


def embed_missing(
    db: Session, company_id: int, embedder: EmbeddingProvider, max_chunks: int
) -> tuple[int, int]:
    """Embed up to `max_chunks` passages that lack a vector, newest filings first.

    Returns (embedded now, still missing). Committed in batches, so an interrupted run keeps
    its progress; a model failure leaves the text searchable by full-text search.
    """
    rows = db.execute(
        _embeddable()
        .join(Filing, Filing.id == FilingChunk.filing_id)
        .where(FilingChunk.company_id == company_id, FilingChunk.embedding.is_(None))
        .order_by(Filing.filed_date.desc(), FilingChunk.id)
        .limit(max_chunks)
    ).all()
    done = 0
    for start in range(0, len(rows), _EMBED_BATCH):
        batch = rows[start : start + _EMBED_BATCH]
        try:
            # The section title gives each passage its context ("Item 1A. Risk Factors: …").
            vectors = embedder.embed_documents([f"{title}: {chunk.text}" for chunk, title in batch])
        except Exception:
            logger.exception("embedding_failed", extra={"chunks": len(batch)})
            break
        for (chunk, _title), vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = embedder.name
        db.commit()
        done += len(batch)
    total, embedded = embedding_counts(db, company_id)
    return done, total - embedded


def _earnings_release(
    db: Session, company: Company, filing: Filing, client: SecEdgarClient
) -> str | None:
    """Text of an earnings 8-K's press release (its EX-99 exhibit), or None.

    The 8-K itself usually just says results were furnished as Exhibit 99.1; the numbers
    and commentary live in the exhibit. A failure here never fails the filing.
    """
    assert company.cik is not None
    try:
        index = client.fetch_filing_document(
            company.cik, filing.accession, f"{filing.accession}-index.htm"
        )
        record_fetch(db, index.meta, status="success")
        exhibit = press_release(parse_exhibits(index.data))
        if exhibit is None:
            return None
        document = client.fetch_filing_document(company.cik, filing.accession, exhibit.document)
        record_fetch(db, document.meta, status="success", record_count=1)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
        logger.warning("exhibit_unavailable", extra={"accession": filing.accession})
        return None
    paragraphs = document_paragraphs(document.data, exhibit.document)
    # Exhibits often open with EDGAR labels ("EX-99.1", the file name, "Exhibit 99.1").
    while paragraphs and _EXHIBIT_LABEL.match(paragraphs[0]):
        paragraphs = paragraphs[1:]
    return "\n\n".join(paragraphs) or None


def load_filing_document(
    db: Session,
    company: Company,
    filing: Filing,
    client: SecEdgarClient,
    *,
    force: bool = False,
) -> tuple[str, str | None]:
    """Fetch the primary document and store its sections and passages.

    Embedding is a separate step (`embed_missing`): it costs ~0.1 s per passage on a CPU,
    while fetching and splitting even a 13 MB 10-K takes about two seconds.
    """
    if (
        not force
        and filing.document_status == "loaded"
        and filing.extractor_version == EXTRACTOR_VERSION
    ):
        return "loaded", None
    if filing.form in INSIDER_FORMS:
        return load_insider_filing(db, company, filing, client, force=force)
    document = filing.primary_document or ""
    if not document.lower().endswith(_TEXT_DOCUMENTS) or "/" in document:
        return _fail(db, filing, "unsupported", "The primary document is not an HTML or text file.")
    if company.cik is None:
        return _fail(db, filing, "unsupported", "The company has no SEC CIK.")
    try:
        result = client.fetch_filing_document(company.cik, filing.accession, document)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
        return _fail(db, filing, "failed", f"SEC document request failed: {error.message}")

    paragraphs = document_paragraphs(result.data, document)
    sections = extract_sections(paragraphs, filing.form, filing.description or filing.form)
    fetch = record_fetch(db, result.meta, status="success", record_count=len(sections))
    if "2.02" in (filing.items or "").split(","):
        release = _earnings_release(db, company, filing, client)
        if release:
            sections.append(
                Section(
                    "exhibit_99", None, "99", "Exhibit 99 · Press release", len(sections), release
                )
            )
    db.execute(delete(FilingSection).where(FilingSection.filing_id == filing.id))

    stored: list[FilingChunk] = []
    for section in sections:
        row = FilingSection(
            filing_id=filing.id,
            key=section.key,
            part=section.part,
            item=section.item,
            title=section.title,
            ordinal=section.ordinal,
            text=section.text,
            char_count=len(section.text),
            text_sha256=hashlib.sha256(section.text.encode()).hexdigest(),
        )
        db.add(row)
        db.flush()
        for chunk in chunk_section(section.text):
            stored.append(
                FilingChunk(
                    section_id=row.id,
                    filing_id=filing.id,
                    company_id=company.id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
    db.add_all(stored)
    filing.document_status = "loaded" if sections else "failed"
    filing.document_error = None if sections else "No text could be extracted from the document."
    filing.document_fetch_id = fetch.id
    filing.extractor_version = EXTRACTOR_VERSION
    filing.loaded_at = utcnow()
    db.commit()
    logger.info(
        "filing_loaded",
        extra={"accession": filing.accession, "sections": len(sections), "chunks": len(stored)},
    )
    return filing.document_status, filing.document_error


def load_insider_filing(
    db: Session, company: Company, filing: Filing, client: SecEdgarClient, *, force: bool = False
) -> tuple[str, str | None]:
    if (
        not force
        and filing.document_status in {"loaded", "not_issuer"}
        and filing.extractor_version == FORM4_PARSER_VERSION
    ):
        return filing.document_status, None
    if company.cik is None or not filing.primary_document:
        return _fail(db, filing, "unsupported", "No ownership document is listed.")
    try:
        result = client.fetch_filing_document(
            company.cik, filing.accession, raw_xml_name(filing.primary_document)
        )
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
        return _fail(db, filing, "failed", f"SEC document request failed: {error.message}")
    try:
        parsed = parse_form4(result.data)
    except Form4Error as error:
        record_fetch(db, result.meta, status="success", record_count=0)
        return _fail(db, filing, "failed", str(error))
    fetch = record_fetch(db, result.meta, status="success", record_count=len(parsed.transactions))
    db.execute(delete(InsiderTransaction).where(InsiderTransaction.filing_id == filing.id))
    filing.document_fetch_id = fetch.id
    filing.extractor_version = FORM4_PARSER_VERSION
    filing.loaded_at = utcnow()
    if parsed.issuer_cik != company.cik:
        # The company filed this as an owner of another issuer's shares.
        filing.document_status = "not_issuer"
        filing.document_error = None
        db.commit()
        return "not_issuer", None

    owners = [
        {
            "cik": o.cik,
            "name": o.name,
            "is_director": o.is_director,
            "is_officer": o.is_officer,
            "is_ten_percent_owner": o.is_ten_percent_owner,
            "officer_title": o.officer_title,
        }
        for o in parsed.owners
    ]
    first = parsed.owners[0] if parsed.owners else None
    db.add_all(
        InsiderTransaction(
            filing_id=filing.id,
            company_id=company.id,
            owners=owners,
            owner_cik=first.cik if first else None,
            owner_name=first.name if first else "Unknown",
            is_director=any(o.is_director for o in parsed.owners),
            is_officer=any(o.is_officer for o in parsed.owners),
            is_ten_percent_owner=any(o.is_ten_percent_owner for o in parsed.owners),
            officer_title=next((o.officer_title for o in parsed.owners if o.officer_title), None),
            is_derivative=t.is_derivative,
            ordinal=t.ordinal,
            security_title=t.security_title,
            transaction_date=t.transaction_date,
            transaction_code=t.transaction_code,
            shares=t.shares,
            price=t.price,
            acquired_disposed=t.acquired_disposed,
            shares_owned_after=t.shares_owned_after,
            ownership=t.ownership,
            ownership_nature=t.ownership_nature,
            rule_10b5_1=parsed.rule_10b5_1,
            underlying_security=t.underlying_security,
            exercise_price=t.exercise_price,
        )
        for t in parsed.transactions
    )
    filing.document_status = "loaded"
    filing.document_error = None
    db.commit()
    return "loaded", None


def pending_filings(
    db: Session, company: Company, forms: Sequence[str], limit: int
) -> list[Filing]:
    """Newest filings of the given forms that are not loaded, or were loaded by an older
    extractor (so an extractor upgrade re-processes them)."""
    current = case((Filing.form.in_(INSIDER_FORMS), FORM4_PARSER_VERSION), else_=EXTRACTOR_VERSION)
    return list(
        db.scalars(
            select(Filing)
            .where(
                Filing.company_id == company.id,
                Filing.form.in_(forms),
                or_(
                    Filing.document_status.in_(["not_loaded", "failed"]),
                    and_(
                        Filing.document_status == "loaded",
                        Filing.extractor_version.is_distinct_from(current),
                    ),
                ),
            )
            .order_by(Filing.filed_date.desc(), Filing.accession.desc())
            .limit(limit)
        )
    )


def load_pending(
    db: Session, company: Company, client: SecEdgarClient, forms: Sequence[str], limit: int
) -> dict[str, int]:
    outcome: dict[str, int] = {}
    for filing in pending_filings(db, company, forms, limit):
        status, _ = load_filing_document(db, company, filing, client)
        outcome[status] = outcome.get(status, 0) + 1
    return outcome


def previous_filing(db: Session, filing: Filing) -> Filing | None:
    """The same company's prior original filing of the same form (10-K → prior 10-K, …).

    Amendments are skipped: they usually restate only part of a report.
    """
    candidates = db.scalars(
        select(Filing)
        .where(
            Filing.company_id == filing.company_id,
            Filing.form == form_family(filing.form),
            Filing.filed_date <= filing.filed_date,
            Filing.id != filing.id,
        )
        .order_by(Filing.filed_date.desc(), Filing.accession.desc())
    )
    return next(
        (
            c
            for c in candidates
            if (c.filed_date, c.accession) < (filing.filed_date, filing.accession)
        ),
        None,
    )
