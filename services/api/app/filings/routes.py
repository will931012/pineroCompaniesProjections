from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.text_diff import diff_paragraphs, summarise
from app.auth.dependencies import AnalystUser, AppSettings, CurrentUser, DbSession
from app.companies.routes import SecClient
from app.companies.service import resolve_security
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import (
    Company,
    Filing,
    FilingChunk,
    FilingSection,
    InsiderTransaction,
    ProviderFetch,
    Security,
)
from app.filings.schemas import (
    DiffBlockOut,
    DiffSummaryOut,
    FilingDetail,
    FilingOut,
    FilingsResponse,
    FormCount,
    HistoryOut,
    IndexDocumentsIn,
    IndexDocumentsOut,
    InsidersResponse,
    InsiderSummary,
    InsiderTransactionOut,
    SearchHitOut,
    SearchResponse,
    SectionDiffOut,
    SectionOut,
    TradeTotals,
    WordOpOut,
)
from app.filings.search import search_passages
from app.filings.sections import EIGHT_K_ITEMS
from app.filings.service import (
    INSIDER_FORMS,
    embed_missing,
    embedding_counts,
    load_filing_document,
    load_filing_history,
    load_pending,
    previous_filing,
    refresh_filing_index,
)
from app.providers.base import ProviderError
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.schemas import source_refs
from app.providers.sec_edgar import ACCESSION, ARCHIVE_URL, SecEdgarClient

router = APIRouter(tags=["filings"])


def get_embedder() -> EmbeddingProvider | None:
    return get_embedding_provider()


Embedder = Annotated[EmbeddingProvider | None, Depends(get_embedder)]


def filing_out(filing: Filing, cik: int | None) -> FilingOut:
    folder = filing.accession.replace("-", "")
    items = [
        f"{item} {EIGHT_K_ITEMS[item]}" if item in EIGHT_K_ITEMS else item
        for item in (filing.items or "").split(",")
        if item.strip()
    ]
    return FilingOut(
        accession=filing.accession,
        form=filing.form,
        filed_date=filing.filed_date,
        accepted_at=filing.accepted_at,
        report_date=filing.report_date,
        description=filing.description,
        items=items,
        size=filing.size,
        document_status=filing.document_status,  # type: ignore[arg-type]
        document_error=filing.document_error,
        sec_url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{filing.accession}-index.htm",
        document_url=ARCHIVE_URL.format(cik=cik, folder=folder, document=filing.primary_document)
        if filing.primary_document and cik
        else None,
    )


def _filing(
    db: Session, company: Company, accession: str, sec: SecEdgarClient, settings: Settings
) -> Filing:
    if not ACCESSION.fullmatch(accession):
        raise ApiError(
            422, "invalid_accession", "Accession numbers look like 0000320193-24-000123."
        )
    query = select(Filing).where(Filing.company_id == company.id, Filing.accession == accession)
    filing = db.scalar(query)
    if filing is None:
        # A direct link may arrive before the index was ever loaded (TTL still applies).
        refresh_filing_index(db, company, sec, settings)
        filing = db.scalar(query)
    if filing is None:
        raise ApiError(404, "filing_not_found", "This filing is not in the company's index.")
    return filing


def _forms(value: str | None) -> list[str]:
    return [f.strip().upper() for f in (value or "").split(",") if f.strip()][:20]


@router.get("/companies/{ticker}/filings", response_model=FilingsResponse)
def list_filings(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    forms: Annotated[str | None, Query(description="Comma-separated, e.g. 10-K,10-Q")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> FilingsResponse:
    company = resolve_security(db, ticker).company
    status, message = refresh_filing_index(db, company, sec, settings)
    selected = _forms(forms)
    query = select(Filing).where(Filing.company_id == company.id)
    if selected:
        query = query.where(Filing.form.in_(selected))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Filing.filed_date.desc(), Filing.accession.desc())
        .limit(limit)
        .offset(offset)
    )
    counts = db.execute(
        select(Filing.form, func.count())
        .where(Filing.company_id == company.id)
        .group_by(Filing.form)
        .order_by(func.count().desc(), Filing.form)
    ).all()
    passages = db.scalar(select(func.count()).where(FilingChunk.company_id == company.id))
    embeddable, embedded = embedding_counts(db, company.id)
    loaded = db.scalar(
        select(func.count()).where(
            Filing.company_id == company.id,
            Filing.document_status == "loaded",
            Filing.form.not_in(INSIDER_FORMS),
        )
    )
    fetch = db.get(ProviderFetch, company.profile_fetch_id) if company.profile_fetch_id else None
    return FilingsResponse(
        ticker=ticker.upper(),
        cik=company.cik,
        status=status,  # type: ignore[arg-type]
        message=message,
        total=total,
        forms=[FormCount(form=form, count=count) for form, count in counts],
        filings=[filing_out(f, company.cik) for f in rows],
        history_loaded=company.filings_history_loaded,
        documents_loaded=loaded or 0,
        passages=passages or 0,
        passages_embeddable=embeddable,
        passages_embedded=embedded,
        sources=source_refs([fetch]),
    )


@router.post("/companies/{ticker}/filings/history", response_model=HistoryOut)
def load_history(ticker: str, _: AnalystUser, db: DbSession, sec: SecClient) -> HistoryOut:
    company = resolve_security(db, ticker).company
    try:
        added = load_filing_history(db, company, sec)
    except ProviderError as error:
        raise ApiError(502, error.code, error.message) from error
    return HistoryOut(ticker=ticker.upper(), added=added)


@router.post("/companies/{ticker}/filings/index", response_model=IndexDocumentsOut)
def index_documents(
    ticker: str,
    body: IndexDocumentsIn,
    _: AnalystUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    embedder: Embedder,
) -> IndexDocumentsOut:
    """Load, split, and embed the newest documents of the given forms for search."""
    company = resolve_security(db, ticker).company
    refresh_filing_index(db, company, sec, settings)
    forms = sorted({f.upper() for f in body.forms} | {f"{f.upper()}/A" for f in body.forms})
    outcome = load_pending(db, company, sec, forms, body.limit)
    embedded, remaining = (
        embed_missing(db, company.id, embedder, body.embed_limit)
        if embedder is not None
        else (0, embedding_counts(db, company.id)[0])
    )
    return IndexDocumentsOut(
        ticker=ticker.upper(),
        outcome=outcome,
        embedded=embedded,
        embedding_remaining=remaining,
        embedding_model=embedder.name if embedder is not None else None,
    )


def _sections(db: Session, filing: Filing) -> list[FilingSection]:
    return list(
        db.scalars(
            select(FilingSection)
            .where(FilingSection.filing_id == filing.id)
            .order_by(FilingSection.ordinal)
        )
    )


@router.get("/companies/{ticker}/filings/{accession}", response_model=FilingDetail)
def filing_detail(
    ticker: str,
    accession: str,
    _: CurrentUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    embedder: Embedder,
) -> FilingDetail:
    company = resolve_security(db, ticker).company
    filing = _filing(db, company, accession, sec, settings)
    load_filing_document(db, company, filing, sec)
    previous = previous_filing(db, filing)
    fetch = db.get(ProviderFetch, filing.document_fetch_id) if filing.document_fetch_id else None
    return FilingDetail(
        ticker=ticker.upper(),
        filing=filing_out(filing, company.cik),
        sections=[
            SectionOut(
                key=s.key,
                title=s.title,
                part=s.part,
                item=s.item,
                char_count=s.char_count,
                text=s.text,
            )
            for s in _sections(db, filing)
        ],
        previous=filing_out(previous, company.cik) if previous else None,
        extractor_version=filing.extractor_version,
        sources=source_refs([fetch]),
    )


@router.get("/companies/{ticker}/filings/{accession}/diff", response_model=SectionDiffOut)
def section_diff(
    ticker: str,
    accession: str,
    _: CurrentUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    embedder: Embedder,
    section: Annotated[str, Query(max_length=40, description="Section key, e.g. item_1a")],
) -> SectionDiffOut:
    company = resolve_security(db, ticker).company
    filing = _filing(db, company, accession, sec, settings)
    load_filing_document(db, company, filing, sec)
    current = db.scalar(
        select(FilingSection).where(
            FilingSection.filing_id == filing.id, FilingSection.key == section
        )
    )
    if current is None:
        raise ApiError(404, "section_not_found", "This filing has no such section.")
    previous = previous_filing(db, filing)
    if previous is None:
        raise ApiError(404, "no_previous_filing", f"No earlier {filing.form} is indexed.")
    load_filing_document(db, company, previous, sec)
    before = db.scalar(
        select(FilingSection).where(
            FilingSection.filing_id == previous.id, FilingSection.key == section
        )
    )
    base = SectionDiffOut(
        ticker=ticker.upper(),
        section_key=section,
        title=current.title,
        current=filing_out(filing, company.cik),
        previous=filing_out(previous, company.cik),
        comparable=before is not None,
        summary=None,
        blocks=[],
    )
    if before is None:
        return base
    blocks = diff_paragraphs(before.text.split("\n\n"), current.text.split("\n\n"))
    summary = summarise(blocks)
    return base.model_copy(
        update={
            "summary": DiffSummaryOut(**summary.__dict__),
            "blocks": [
                DiffBlockOut(
                    kind=b.kind,
                    before=b.before,
                    after=b.after,
                    words=[WordOpOut(op=w.op, text=w.text) for w in b.words],
                )
                for b in blocks
            ],
        }
    )


def _primary_tickers(db: Session, company_ids: set[int]) -> dict[int, str]:
    rows = db.execute(
        select(Security.company_id, Security.ticker)
        .where(Security.company_id.in_(company_ids), Security.is_active)
        .order_by(Security.company_id, Security.id)
    ).all()
    tickers: dict[int, str] = {}
    for company_id, ticker in rows:
        tickers.setdefault(company_id, ticker)
    return tickers


@router.get("/search/filings", response_model=SearchResponse)
def search_filings(
    _: CurrentUser,
    db: DbSession,
    embedder: Embedder,
    q: Annotated[str, Query(min_length=2, max_length=300)],
    tickers: Annotated[str | None, Query(description="Comma-separated tickers")] = None,
    forms: Annotated[str | None, Query(description="Comma-separated forms")] = None,
    since: date | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    company_ids = [resolve_security(db, t).company_id for t in _forms(tickers)] if tickers else None
    selected = _forms(forms)
    if selected:
        selected = sorted({*selected, *(f"{f}/A" for f in selected)})
    hits = search_passages(
        db, q, embedder=embedder, company_ids=company_ids, forms=selected or None, since=since,
        limit=limit,
    )  # fmt: skip
    ids = {h.company_id for h in hits}
    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(ids)))}
    tickers_by_id = _primary_tickers(db, ids)
    return SearchResponse(
        query=q,
        mode="hybrid" if embedder is not None else "text",
        embedding_model=embedder.name if embedder is not None else None,
        hits=[
            SearchHitOut(
                ticker=tickers_by_id.get(h.company_id),
                company_name=companies[h.company_id].legal_name,
                filing=filing_out(h.filing, companies[h.company_id].cik),
                section_key=h.section.key,
                section_title=h.section.title,
                char_start=h.char_start,
                char_end=h.char_end,
                snippet=h.snippet,
                score=h.score,
                text_rank=h.text_rank,
                vector_rank=h.vector_rank,
                vector_distance=h.vector_distance,
            )
            for h in hits
        ],
    )


def _totals(rows: list[InsiderTransaction]) -> TradeTotals:
    value = sum(
        (r.shares * r.price for r in rows if r.shares is not None and r.price is not None),
        Decimal(0),
    )
    return TradeTotals(
        transactions=len(rows),
        shares=float(sum((r.shares or Decimal(0) for r in rows), Decimal(0))),
        value=float(value),
        insiders=len({r.owner_cik or r.owner_name for r in rows}),
    )


@router.get("/companies/{ticker}/insiders", response_model=InsidersResponse)
def insiders(
    ticker: str,
    _: CurrentUser,
    db: DbSession,
    sec: SecClient,
    settings: AppSettings,
    embedder: Embedder,
    months: Annotated[int, Query(ge=1, le=120)] = 12,
    load: Annotated[int, Query(ge=0, le=60, description="Form 4s to fetch now")] = 25,
) -> InsidersResponse:
    company = resolve_security(db, ticker).company
    status, message = refresh_filing_index(db, company, sec, settings)
    if load:
        load_pending(db, company, sec, INSIDER_FORMS, load)
    since = (utcnow() - timedelta(days=round(months * 30.4375))).date()
    rows = db.execute(
        select(InsiderTransaction, Filing)
        .join(Filing, Filing.id == InsiderTransaction.filing_id)
        .where(
            InsiderTransaction.company_id == company.id,
            InsiderTransaction.transaction_date >= since,
        )
        .order_by(
            InsiderTransaction.transaction_date.desc(),
            Filing.filed_date.desc(),
            InsiderTransaction.is_derivative,
            InsiderTransaction.ordinal,
        )
    ).all()
    counts = dict(
        db.execute(
            select(Filing.document_status, func.count())
            .where(Filing.company_id == company.id, Filing.form.in_(INSIDER_FORMS))
            .group_by(Filing.document_status)
        ).all()
    )
    open_market = [t for t, _f in rows if not t.is_derivative]
    return InsidersResponse(
        ticker=ticker.upper(),
        status=status,  # type: ignore[arg-type]
        message=message,
        filings_loaded=counts.get("loaded", 0),
        filings_pending=counts.get("not_loaded", 0) + counts.get("failed", 0),
        summary=InsiderSummary(
            months=months,
            since=since,
            purchases=_totals([t for t in open_market if t.transaction_code == "P"]),
            sales=_totals([t for t in open_market if t.transaction_code == "S"]),
        ),
        transactions=[
            InsiderTransactionOut(
                accession=f.accession,
                form=f.form,
                filed_date=f.filed_date,
                sec_url=filing_out(f, company.cik).sec_url,
                owner_name=t.owner_name,
                owner_cik=t.owner_cik,
                joint_owners=max(len(t.owners) - 1, 0),
                is_director=t.is_director,
                is_officer=t.is_officer,
                is_ten_percent_owner=t.is_ten_percent_owner,
                officer_title=t.officer_title,
                is_derivative=t.is_derivative,
                security_title=t.security_title,
                transaction_date=t.transaction_date,
                transaction_code=t.transaction_code,
                shares=float(t.shares) if t.shares is not None else None,
                price=float(t.price) if t.price is not None else None,
                value=float(t.shares * t.price)
                if t.shares is not None and t.price is not None
                else None,
                acquired_disposed=t.acquired_disposed,
                shares_owned_after=float(t.shares_owned_after)
                if t.shares_owned_after is not None
                else None,
                ownership=t.ownership,
                ownership_nature=t.ownership_nature,
                rule_10b5_1=t.rule_10b5_1,
                underlying_security=t.underlying_security,
                exercise_price=float(t.exercise_price) if t.exercise_price is not None else None,
            )
            for t, f in rows[:500]
        ],
    )
