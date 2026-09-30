# Phase 3 Report — SEC filings and retrieval

Completed 2026-09-30. 13F institutional holdings were deferred to Phase 6 (see below).

## Implemented

**Backend (`services/api`)**
- **Filing index** (`filings`). The SEC submissions response already fetched for company
  profiles also lists the ~1,000 most recent filings. Both are stored from one request and
  refreshed every 6 hours (`FILINGS_TTL_HOURS`). Older history pages load on request.
- **Documents** (`app/filings`). A filing's primary document is fetched on first view and
  turned into plain-text paragraphs:
  - scripts, styles, hidden inline-XBRL headers, page numbers and "Table of Contents" links
    are removed;
  - table rows stay on one line, cells separated by " | ".

  Raw HTML is not stored; every filing links to its sec.gov original.
- **Section extraction** (`sections.py`, extractor `2026.09-3`) splits 10-K, 10-Q (keyed by
  part) and 8-K reports into items:
  - **Table-of-contents entries:** for each item, the heading that starts the longest run of
    text wins.
  - **Order:** items must follow the form's official order. The largest in-order subset is
    kept and the rest are re-chosen between their neighbours.
  - **Running page headers** such as a repeated "PART II / Item 7" are merged and removed.
  - **Reports not organised under item headings** (JPMorgan's 10-K and 10-Q) are kept as a
    single "document" section rather than mislabelled.
  - **Cleanup:** signature blocks and part headings are excluded from item text.
- **Diffs** (`analytics/text_diff.py`, pure and deterministic) compare a section with the same
  section of the previous original filing of that form:
  - unchanged, added, removed and changed paragraphs;
  - a word-level diff for changed paragraphs (paired at ≥ 50% word overlap);
  - summary counts.
- **Form 4** (`form4.py`) parses both transaction tables as reported:
  - owners, roles, transaction code, shares, price, acquired/disposed, holdings after,
    direct/indirect, and the 10b5-1 flag;
  - safe parsing: no entity resolution, no network access;
  - a Form 4 the company filed as an owner of *another* issuer (Berkshire has many) is marked
    `not_issuer` and excluded.
- **Search** (`filings/search.py`) works over passages of 1,000–2,000 characters
  (`filing_chunks`), with offsets into their section:
  - **Keyword:** PostgreSQL full-text search (English, GIN). Passages matching all words rank
    first; passages matching any word fill in behind them.
  - **Semantic:** cosine similarity in pgvector (HNSW, iterative scans), using vectors from
    the active model only.
  - **Ranking:** the two are combined with reciprocal rank fusion (k = 60). Each hit returns
    its filing, section and character span, so the web app opens the exact passage.
- **Embeddings** sit behind an `EmbeddingProvider` port:
  - **Default:** `FastEmbedProvider` runs the open model BAAI/bge-small-en-v1.5 (384
    dimensions) locally through ONNX Runtime. No API key, and text never leaves the server.
    `EMBEDDING_PROVIDER=none` leaves search to keywords.
  - **Separate step:** embedding costs about 0.1 s per passage on a CPU, while fetching and
    splitting even a 13 MB 10-K takes about 2 s. Viewing a filing is therefore fast, and
    embedding runs via "Index for search" or the CLI in bounded batches, newest filings first.
  - **Keyword only:** financial statements and exhibits are not embedded; they are mostly
    tables.
- **Upgrades:** documents loaded by an older extractor are picked up again by the next
  indexing run.
- **Endpoints:**
  - `GET /companies/{t}/filings`
  - `POST /companies/{t}/filings/history` (analyst)
  - `POST /companies/{t}/filings/index` (analyst)
  - `GET /companies/{t}/filings/{accession}`
  - `GET /companies/{t}/filings/{accession}/diff?section=`
  - `GET /companies/{t}/insiders?months=&load=`
  - `GET /search/filings?q=&tickers=&forms=&since=`
- **CLI:**
  - `sync-filings --tickers … [--forms] [--limit] [--insiders] [--history]`
  - `prepare-embeddings`: the API Dockerfile runs it, so the model (~130 MB) is built into the
    image.
- **Migration `0004_filings`:**
  - creates the `vector` extension;
  - adds `filings`, `filing_sections`, `filing_chunks` and `insider_transactions`;
  - adds two columns to `companies`.

**Web (`apps/web`)**
- **SEC tab:** company-scoped search (hybrid, cited, highlighted), index status and controls,
  the filing list with form filters, pagination, and links to the in-app viewer and sec.gov.
- **Filing viewer** (`/companies/{ticker}/filings/{accession}`): section navigator (a select
  on phones), section text as filed, the cited passage highlighted and scrolled into view, and
  "Compare with previous" (unchanged runs collapsed, removals struck through, word-level
  changes).
- **Insiders tab:** open-market purchase and sale totals for 6, 12 or 24 months, and every
  transaction with its role, code label, 10b5-1 badge and sec.gov link. Not-yet-fetched
  Form 4s load on request.
- The dashboard, top bar and navigation report Phase 3. The Ownership tab is labelled P6.

## Verification (run locally on 2026-09-30)

| Check | Result |
|---|---|
| API tests (pytest, PostgreSQL 18 + pgvector 0.8.6) | 154 passed (86 unit, 68 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (82 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 43 passed |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK |
| `npm audit` | 0 vulnerabilities |
| Live SEC ingestion | AAPL, MSFT, NVDA, JPM, BRK-B: 10 newest documents and 40 Form 4s each; 2,944 passages embedded |
| Browser end to end (headless Edge, desktop + 390px) | search → cited passage → viewer → 10-K diff → insiders; no page errors, no overflow |

**Section extraction on real filings (latest 10-K)**

| Company | Risk Factors | MD&A | Financial statements |
|---|---|---|---|
| Apple | 68k characters | 18k | 63k |
| Microsoft | 81k | 51k | 103k |
| NVIDIA | 114k | 34k | in Item 15 (NVIDIA's layout) |
| Berkshire | 21k | 120k | 180k |
| JPMorgan | one "document" section (no item headings in the body) | | |

**Search on real questions**

| Question | Keyword only | Hybrid |
|---|---|---|
| Apple: "dependence on manufacturing partners outside the US" | none before the any-word fallback | Item 1A passage on outsourcing partners |
| Microsoft: "relationship with OpenAI" | none before the any-word fallback | MD&A passage on the OpenAI partnership |
| NVIDIA: "restrictions on selling AI chips to China" | Risk Factors (10-K and 10-Qs) | same |
| JPMorgan: "credit card net charge-off rate" | Card net charge-off rate 3.31% | same |
| Berkshire: "insurance float" | MD&A float passage | same |

**Other behaviour checked**
- **Apple Risk Factors, FY2025 vs FY2024:** 18 paragraphs added, 27 removed, 54 changed and
  46 unchanged.
- **NVIDIA insiders, last 12 months:** 314 open-market sales totalling $2.17B from 12 insiders;
  no open-market purchases.
- **Filings by the company as an owner:** 32 of the 69 Berkshire Form 4s processed are
  marked `not_issuer`.

## Deploying this phase (Railway)

Phases 2 and 3 are both undeployed. Railway runs Phase 1 at migration `0002`.

```powershell
railway up services/api --path-as-root --service api --ci   # image now includes the embedding model
railway up apps/web    --path-as-root --service web --ci
railway ssh --service api alembic upgrade head               # 0003 and 0004 (creates the vector extension)
railway ssh --service api python -m app.cli sync-fundamentals --tickers AAPL,MSFT,NVDA
railway ssh --service api python -m app.cli sync-filings --tickers AAPL,MSFT,NVDA
```

The Railway Postgres image has pgvector 0.8.6 available. The API image is about 250 MB larger
(ONNX Runtime plus the model). The model uses about 150–250 MB of memory once loaded, and only
when embedding.

## Known limitations and technical debt

1. **13F holdings deferred to Phase 6.** 13F lists positions by CUSIP, and SEC publishes no
   CUSIP→ticker mapping.
2. **Indexing runs in the request.** "Index for search" embeds up to 200 passages per request
   (~20–25 s on this machine). Bulk work belongs to the CLI until the Phase 4 job worker
   exists.
3. **The SEC rate limit is per process.** The CLI and the API each throttle to 8 requests/s,
   so running both at once can briefly exceed SEC's 10/s. The Phase 4 worker should own all
   SEC traffic.
4. **Only primary documents are read.** 8-K exhibits (for example the EX-99.1 earnings press
   release) are not loaded, so 8-K sections are often short. Earnings releases belong with
   Phase 4 events.
5. **Heuristic extraction.** It is checked on six large filers. Unusual layouts can still
   produce empty or merged items; extraction falls back to one "document" section rather than
   mislabel text. Tables are flattened to text.
6. **Proxy statements and other forms** are shown as one section, with no item structure.
7. **Insider totals** count only non-derivative P and S trades with a reported price, and use
   the first reporting owner's name for joint filings (the others are counted in
   `joint_owners`).
8. **Embeddings are English-only.** BGE-small has a 512-token window; passages are sized to
   fit.
9. **Open items from earlier phases:** market cap after splits, bank line items, CSP,
   password reset/MFA, GitHub auto-deploy on Railway, and the `AUTH_DISABLED` note in the
   README.
