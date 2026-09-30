# Phase 2 Report — Fundamentals

Completed 2026-09-30.

## Implemented

**Backend (`services/api`)**
- SEC XBRL `companyfacts` adapter (`providers/sec_edgar.py`). It keeps only the tracked
  concepts from 10-K/10-Q filings (and their amendments). Each distinct value is stored once,
  dated by the **earliest filing that made it public**. A restatement becomes a new row;
  rows are never updated in place.
- Migration `0003_fundamentals`: `financial_facts` (point-in-time facts with accession,
  form, filed date and `fetch_id`) and `company_metrics` (latest screenable value per
  metric, with its basis, period end and availability date).
- Concept map (`fundamentals/concepts.py`, mapping version `2026.09-2`). It maps 36
  canonical line items across the income statement, balance sheet and cash flow statement,
  with ordered us-gaap candidates. Every cell records which concept supplied it.
- Statement builder (`fundamentals/statements.py`) for annual and quarterly statements,
  rebuilt as of any past date:
  - **Restatements:** the latest value public on the as-of date wins.
  - **Derived quarters:** Q4 and other missing quarters come from year-to-date values of the
    same concept, and each derived cell says so.
  - **Fiscal-year labels:** SEC's `fy` describes the filing, not the fact, so it labels only
    the year a filing actually reports on. Comparative years use the issuer's own offset,
    which keeps Target's year ending 2025-02-01 as FY2024.
  - **In-progress year:** 10-Qs after the last 10-K form a partial year, so quarterly
    statements and TTM reach the latest filing.
  - **Stock splits:** detected from restated share counts. Earlier share and per-share
    values are restated onto the current basis, and those cells say so.
- Deterministic formulas (`analytics/fundamentals.py`) and 36 historical metrics
  (`fundamentals/metrics.py`):
  - **Growth:** YoY, QoQ, 3- and 5-year CAGR.
  - **Margins:** gross, operating, EBITDA, net, FCF.
  - **Returns:** ROE, ROA, ROIC.
  - **Liquidity:** current and quick ratios, working capital and its changes.
  - **Leverage:** debt/equity, debt/EBITDA, net debt/EBITDA, interest coverage.
  - **Cash flow:** OCF, capex, FCF.
  - **Shareholder metrics:** dilution, SBC/revenue, dividend growth, payout, buybacks.

  An undefined ratio returns nothing; nothing is defaulted.
- Latest snapshot of 30 screenable metrics (`fundamentals/snapshot.py`):
  - **Basis:** TTM when four contiguous quarters exist, else the latest fiscal year, and each
    value states the basis it used.
  - **Market cap:** latest close × share count. The share count comes from the cover page,
    else the balance sheet, else weighted-average basic shares, and is never more than 400
    days old.
  - **Price-based metrics:** EV, P/E, P/S, P/B, EV/EBITDA, FCF/dividend/buyback yields, 1–12
    month momentum and 1-year volatility.
- Endpoints:
  - `GET /companies/{ticker}/fundamentals?period=&as_of=&limit=`
  - `GET /companies/{ticker}/metrics`
  - `GET /companies/{ticker}/peers?tickers=`: SIC code, falling back to the two-digit major
    group, ranked by revenue, plus user-selected peers.
  - `GET /screener/metrics`, `GET /screener/sectors`, `POST /screener`
  - `POST /admin/ingestion/fundamentals` (audited)
- CLI `sync-fundamentals --tickers … | --all-active [--force]`. Bulk sync also loads each
  company's SEC profile, because peers and sectors need the SIC code.
- Refreshing prices or facts recomputes the company's metrics.

**Web (`apps/web`)**
- Company page tabs: Overview, **Financials** and **Peers** are live; the rest still show
  their phase. The selected tab is kept in the URL (`?tab=financials`).
- Financials tab:
  - Annual/quarterly toggle and an **As of** date for point-in-time statements.
  - Historical column charts: revenue, net income, FCF, operating margin, ROIC, diluted EPS.
  - The three statements, with a per-cell tooltip showing the XBRL concept, accession, public
    date and any derivation; derived and adjusted cells are italic.
  - Metric history grouped by category, each metric with its formula.
  - Mapping and formula versions, and sources.
- Company header shows market cap and EV, with basis and dates on hover. The Overview side
  panel shows key metrics.
- Peers tab: comparison table with extra-ticker input.
- Screener page: up to 12 min/max filters (percent entered as %), SIC division, sort,
  pagination, links into each company's Financials tab.
- Admin console: "SEC financial data" panel to load up to 25 tickers.
- Chart colours validated with the dataviz palette checker: lightness, chroma,
  colour-blind separation, and 3:1 contrast all pass.

## Verification (run locally on 2026-09-30)

| Check | Result |
|---|---|
| API tests (pytest, real PostgreSQL 18) | 127 passed (69 unit, 58 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (69 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 36 passed |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK (`/screener` is a real route) |
| `npm audit` | 0 vulnerabilities |
| OpenAPI contract | regenerated; web types match |
| Live SEC ingestion | 18 companies (AAPL, MSFT, GOOGL, BRK-B, NVDA, JPM, KO, AMZN, META, WMT, TGT, XOM, ORCL, ADBE, CRM, IBM, INTC, AMD) |
| Browser end to end (headless Edge, desktop + 390px) | no page errors, no horizontal overflow; only 503s from the unconfigured price provider |

**Checked against reported figures**

| Company | Period | Revenue | Net income | Diluted EPS |
|---|---|---|---|---|
| Apple | FY2024 | $391,035M | $93,736M | $6.08 |
| Alphabet | FY2024 | $350,018M | $100,118M | $8.04 |
| NVIDIA | FY2025 | $130,497M | $72,880M | $2.94 |
| Microsoft | FY2025 | $281,724M | $101,832M | — |
| Walmart | FY2025 | $674,538M | — | — |

**Behaviour checked on real data**
- **Splits:** each was detected with its exact ratio and dated to the first post-split
  filing: Apple 7:1 (2014) and 4:1 (2020); NVIDIA 4:1 (2021) and 10:1 (2024); Amazon and
  Alphabet 20:1 (2022); Walmart 3:1 (2024); Coca-Cola 2:1 (2012). None were falsely found for
  Microsoft, JPMorgan or Meta.
- **Point in time:** as of 2020-01-15, Apple's latest quarter is Q4 FY2019. Its Q1 FY2020
  10-Q was filed on 2020-01-29, so it is correctly excluded.
- **Peers:** Alphabet's peers are Microsoft, Meta, Oracle, Salesforce and Adobe. Only Meta
  shares Alphabet's exact SIC code, so the two-digit group 73 is used.
- **Screener:** revenue growth > 10% and operating margin > 15% returns ADBE, AMD, GOOGL,
  AAPL, META, MSFT, NVDA and ORCL.

Not verified here:
- Price-based metrics against live prices, because Tiingo is not configured locally. Their
  formulas are covered by tests with stored bars.
- Docker images (Docker is not installed on this machine).

## Deploying this phase (Railway)

Deploys and migrations are manual (see [deployment.md](deployment.md)):

```powershell
railway up services/api --path-as-root --service api --ci
railway up apps/web    --path-as-root --service web --ci
railway ssh --service api alembic upgrade head          # applies 0003_fundamentals
railway ssh --service api python -m app.cli sync-fundamentals --tickers AAPL,MSFT,NVDA
```

`--all-active` loads every listed company: two SEC requests each at ≤ 8 requests/s, so
roughly an hour for the full directory. Companies also load on demand the first time
someone opens their Financials tab.

## Known limitations and technical debt

1. **Market cap after a split.** Between a split and the next 10-Q, prices are post-split
   but the latest SEC share count is not. Market cap and multiples are wrong in that window.
   Fix: store the price provider's split factor (Tiingo sends one) and apply it.
2. **Multi-class issuers.** companyfacts omits per-class cover-page counts:
   - Alphabet falls back to its balance-sheet shares and Meta to weighted-average basic
     shares; the basis label says which.
   - Berkshire has no current share count in companyfacts, so it shows no market cap.
   - When a count is summed across classes, one class's price is used for all classes.
3. **Source-data errors are shown as filed.** Some early filings (2009–2011) tagged share
   counts in thousands. For example, NVIDIA's FY2008–FY2009 weighted shares, with a
   +100,164% "share change" in FY2010. A ×1000 correction was tried and removed: it misfired
   on Apple, and deduplicated storage lacks the evidence to do it safely.
4. **Banks and insurers.** Operating income, gross margin and ROIC are blank for most
   financials: they don't report those concepts. Bank-specific line items (net interest
   income, provisions, deposits) are not mapped yet.
5. **Refresh is still inline.** The first Financials view of a company fetches companyfacts
   in the request (30 s timeout). Scheduled refreshes belong to the Phase 4 worker, like SEC
   profiles (Phase 1 debt item 3).
6. **Screener universe.** Only companies with loaded fundamentals can be screened; the
   results header shows the universe size. Run `sync-fundamentals --all-active` for full
   coverage.
7. **Peers** use SIC codes, which are coarse (e.g. 7370 covers Alphabet and Meta but not
   Microsoft). Industry taxonomies such as GICS are licensed and not used.
8. **Metric snapshots** are latest-only. Historical screening (point-in-time metric panels)
   is part of the Phase 6 feature store.
9. Phase 1 items still open: CSP header, password reset/MFA, `/metrics` network
   restriction, the httpx2 deprecation, and GitHub auto-deploy on Railway.
