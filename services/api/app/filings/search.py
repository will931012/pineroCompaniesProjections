"""Search indexed filing passages. Every hit is a stored passage with its exact location.

Two rankings are combined with reciprocal rank fusion (k = 60): PostgreSQL full-text search
(`websearch_to_tsquery`, English) and, when an embedding provider is configured, cosine
similarity against passages embedded by that same model. Either ranking alone still works.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, Select, Text, cast, func, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from app.db.models import Filing, FilingChunk, FilingSection
from app.providers.embeddings import EmbeddingProvider

RRF_K = 60
CANDIDATES = 50
# Snippet highlight markers (Unicode private use), rendered as <mark> by the web app.
MARK_START = ""
MARK_END = ""


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    company_id: int
    filing: Filing
    section: FilingSection
    char_start: int
    char_end: int
    snippet: str
    score: float
    text_rank: int | None
    vector_rank: int | None
    vector_distance: float | None


def _filtered(
    query: Select, company_ids: Sequence[int] | None, forms: Sequence[str] | None,
    since: date | None,
) -> Select:  # fmt: skip
    query = query.join(Filing, Filing.id == FilingChunk.filing_id)
    if company_ids:
        query = query.where(FilingChunk.company_id.in_(company_ids))
    if forms:
        query = query.where(Filing.form.in_(forms))
    if since:
        query = query.where(Filing.filed_date >= since)
    return query


def search_passages(
    db: Session,
    query: str,
    *,
    embedder: EmbeddingProvider | None,
    company_ids: Sequence[int] | None = None,
    forms: Sequence[str] | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    tsquery = func.websearch_to_tsquery("english", query)
    # Passages matching any word, used only to fill in behind passages matching all words.
    any_word = cast(
        func.replace(cast(func.plainto_tsquery("english", query), Text), "&", "|"), TSQUERY
    )

    def text_matches(condition: ColumnElement, exclude: list[int]) -> list[int]:
        ranked = (
            _filtered(select(FilingChunk.id), company_ids, forms, since)
            .where(FilingChunk.search.op("@@")(condition))
            .order_by(func.ts_rank_cd(FilingChunk.search, condition).desc(), FilingChunk.id)
            .limit(CANDIDATES)
        )
        if exclude:
            ranked = ranked.where(FilingChunk.id.not_in(exclude))
        return list(db.scalars(ranked))

    text_ids = text_matches(tsquery, [])
    if len(text_ids) < CANDIDATES:
        text_ids += text_matches(any_word, text_ids)[: CANDIDATES - len(text_ids)]

    vector_ids: list[int] = []
    distances: dict[int, float] = {}
    if embedder is not None:
        vector = embedder.embed_query(query)
        distance = FilingChunk.embedding.cosine_distance(vector)
        # Keep scanning the HNSW graph until enough rows pass the filters (pgvector ≥ 0.8).
        db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        db.execute(text("SET LOCAL hnsw.ef_search = 100"))
        rows = db.execute(
            _filtered(select(FilingChunk.id, distance.label("d")), company_ids, forms, since)
            .where(
                FilingChunk.embedding.is_not(None),
                FilingChunk.embedding_model == embedder.name,
            )
            .order_by(distance)
            .limit(CANDIDATES)
        ).all()
        vector_ids = [row.id for row in rows]
        distances = {row.id: float(row.d) for row in rows}

    text_rank = {chunk_id: rank for rank, chunk_id in enumerate(text_ids, start=1)}
    vector_rank = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    scores = {
        chunk_id: sum(1 / (RRF_K + r[chunk_id]) for r in (text_rank, vector_rank) if chunk_id in r)
        for chunk_id in {*text_ids, *vector_ids}
    }
    top = sorted(scores, key=lambda c: (-scores[c], c))[:limit]
    if not top:
        return []

    headline = func.ts_headline(
        "english",
        FilingChunk.text,
        any_word,
        f"StartSel={MARK_START}, StopSel={MARK_END}, MaxWords=45, MinWords=20, MaxFragments=2",
    )
    rows = db.execute(
        select(FilingChunk, FilingSection, Filing, headline.label("snippet"))
        .join(FilingSection, FilingSection.id == FilingChunk.section_id)
        .join(Filing, Filing.id == FilingChunk.filing_id)
        .where(FilingChunk.id.in_(top))
    ).all()
    by_id = {row.FilingChunk.id: row for row in rows}
    hits = []
    for chunk_id in top:
        row = by_id[chunk_id]
        chunk: FilingChunk = row.FilingChunk
        snippet = row.snippet if MARK_START in (row.snippet or "") else chunk.text[:320]
        hits.append(
            SearchHit(
                chunk_id=chunk_id,
                company_id=chunk.company_id,
                filing=row.Filing,
                section=row.FilingSection,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                snippet=snippet,
                score=scores[chunk_id],
                text_rank=text_rank.get(chunk_id),
                vector_rank=vector_rank.get(chunk_id),
                vector_distance=distances.get(chunk_id),
            )
        )
    return hits
