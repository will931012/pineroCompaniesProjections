# Pinero Research Platform — Architecture

Status: Phase 4 (News, events, alerts) complete. Last updated 2026-09-30.

## 1. Starting point (what existed)

Phase 1 was taken over from an earlier skeleton plus a partial Codex session. Reused:

| Existing piece | Outcome |
|---|---|
| FastAPI app, error envelope `{error:{code,message,request_id}}` | Kept; extended with validation details, 404/500 handlers |
| Company/security tables (migration `0001`) | Kept; extended by `0002` |
| Fail-closed market-data stub (`provider_not_configured`) | Kept as behaviour; replaced by a real provider port + Tiingo adapter |
| OIDC login via Authlib (Codex) | Kept as optional SSO; sessions moved from Redis to PostgreSQL next to a `users` table |
| Next.js 16 app, visual design tokens, Lightweight Charts | Kept; app restructured around a session-aware shell |
| Docker Compose, GitHub Actions, docs | Extended |

Superseded modules still on disk pending deletion (excluded from lint/type-check):
`services/api/app/api/`, `app/security/`, `app/schemas/`, `app/market_data/provider.py`.

## 2. Target architecture

A **modular monolith**: one deployable API with hard internal boundaries, one web app.
Each domain package owns its models, service functions, schemas, and routes, and talks to
other domains only through service functions, so any package can later be extracted into a
service behind the same contract.

```text
Browser ──▶ Next.js (apps/web) ──/api/v1 rewrite──▶ FastAPI (services/api)
                 same origin, first-party cookie        │
                                                        ├── auth/        identity, sessions, RBAC, OIDC
                                                        ├── companies/   directory search, profiles
                                                        ├── market_data/ bars, caching, summaries
                                                        ├── workspace/   watchlists (later: theses, portfolios)
                                                        ├── admin/       users, ingestion, audit, provider log
                                                        ├── system/      health, readiness, status
                                                        ├── ingestion/   batch jobs (SEC directory sync)
                                                        ├── analytics/   deterministic formulas (pure functions)
                                                        ├── providers/   external adapters + provenance recording
                                                        └── core/, db/, audit/
                                                        ▼
                                          PostgreSQL (system of record) · Redis (rate limits, later jobs/cache)
```

### Mapping to the requested service layout

The requested `/services/*` directories are packages inside the monolith until scale justifies
extraction. Planned locations:

| Requested service | Package (now or planned) | Phase |
|---|---|---|
| market-data | `app/market_data`, `app/providers/market_data` | 1 ✔ |
| fundamentals | `app/fundamentals`, `app/analytics/fundamentals.py`, `app/screener` (+ SEC XBRL companyfacts adapter) | 2 ✔ |
| sec | `app/filings`, `app/providers/sec_edgar.py`, `app/analytics/text_diff.py` | 3 ✔ |
| news | `app/events`, `app/providers/gdelt.py` | 4 ✔ |
| nlp | `app/providers/embeddings.py` (embedding port, 3 ✔), `app/nlp` (LLM port, RAG) | 3–8 |
| valuation | `app/valuation` → `packages/financial-models` | 5 |
| quant, ml | `app/quant` + top-level `ml/` (features, training, evaluation) | 6 |
| backtesting | `app/backtesting` | 7 |
| portfolio, risk | `app/portfolio`, `app/risk` | 9 |
| paper trading | `app/execution` (PaperBrokerAdapter only) | 10 |
| alerts | `app/alerts`, `app/jobs`, `app/worker.py`, `app/providers/email.py` | 4 ✔ |
| packages/types | `packages/types/openapi.json` (generated contract) | 1 ✔ |

### Invariants

- **No invented numbers.** Every externally sourced value references a `provider_fetches` row
  (provider, URL, retrieval time, license, latency, outcome, content hash). Missing data is shown
  as missing. Provider rows that fail validation are rejected and counted, never repaired.
- **Deterministic calculations** live in `app/analytics` as pure functions over `Decimal`, with
  formula docstrings and unit tests. LLMs will never compute or choose financial values.
- **Point-in-time.** Stored data keeps provider values as reported plus retrieval timestamps;
  securities are never deleted (delisted = inactive), preserving survivorship-free history.
- **Separation of concerns.** Facts, calculations, model outputs, and AI interpretation are
  distinct systems with distinct outputs; nothing combines them into an opaque score.
- **No real-money execution.** Broker adapters are a future port; only a paper adapter will ship.

## 3. Database schema

Implemented (migrations `0001`–`0004`):

| Table | Purpose / key constraints |
|---|---|
| `provider_fetches` | Provenance for every provider request. Index (provider, dataset, subject, retrieved_at) |
| `companies` | Registrant identity; `cik` unique; SIC classification; `directory_fetch_id`, `profile_fetch_id` |
| `securities` | Listings; **partial unique index** (ticker, exchange) `WHERE is_active`, NULLS NOT DISTINCT; first/last seen |
| `daily_prices` | PK (security_id, provider, trade_date); raw + provider-adjusted OHLCV, dividends, split factor; `fetch_id` |
| `users` | Email (lowercase check), argon2id hash (nullable for SSO-only), role check (viewer/analyst/admin), lockout |
| `user_identities` | OIDC (issuer, subject) unique → user |
| `user_sessions` | SHA-256 of cookie token (unique), CSRF token, absolute + idle expiry, revocation |
| `audit_events` | Append-only security log; indexes (actor, time), (action, time) |
| `watchlists`, `watchlist_items` | Owner-scoped; unique (owner, name) |
| `financial_facts` | One row per distinct XBRL value, dated by the first filing that made it public (`filed_date`); unique (company, taxonomy, concept, unit, start, end, value) NULLS NOT DISTINCT; restatements are new rows; `fetch_id` |
| `company_metrics` | PK (company, metric); latest screenable value with basis label, period end, availability date, price-derived flag, formula version |

| `filings` | A company's EDGAR filing index: accession unique per company, form, filed date, acceptance time, period, primary document, 8-K items; document load status, extractor version, `index_fetch_id` / `document_fetch_id` |
| `filing_sections` | A filing's items as plain text (10-K Item 1A, 10-Q Part II Item 1A, 8-K Item 2.02, …); unique (filing, key); SHA-256 of the text |
| `filing_chunks` | Passages of 1,000–2,000 characters with offsets into their section; generated English `tsvector` (GIN) and a 384-dimension pgvector embedding (HNSW, cosine) with the model that produced it |
| `insider_transactions` | Form 4 table rows as reported: owners (JSONB), roles, code, shares, price, acquired/disposed, holdings after, direct/indirect, 10b5-1 flag, derivative details |

Statements are not stored: they are rebuilt from `financial_facts` for any as-of date. Filing
text is stored; raw HTML is not (each filing links to its sec.gov original).

Phase 4 tables (migration `0005`): `jobs` (work queue; partial unique dedupe key for active
jobs), `news_items` (headline, outlet, link, seen time, embedding; unique per provider URL),
`news_mentions` (company link with method and confidence), `events` (8-K or news; type,
novelty, story cluster, evidence JSON, classifier version), `alert_rules` (owner, kind,
params, tickers/watchlist scope), `alerts` (unique per rule and subject; email outcome).

Planned tables by phase: `institutional_holdings` (6, deferred from 3); `transcripts`;
`valuations` (5); `macro_data`, `features`,
`predictions`, `prediction_outcomes`, `model_versions` (6); `backtests` (7); `investment_theses` (8);
`portfolios`, `positions` (9); `orders`, `trades` (10).
Every externally sourced table carries `fetch_id` plus an **availability timestamp** (when the
information became public) distinct from its **effective/period date**, which is what makes
point-in-time feature reconstruction possible. TimescaleDB is deferred until price/feature volume
warrants hypertables; the compose image already ships pgvector.

## 4. API contracts (v1)

All under `/api/v1`, JSON, ISO-8601 UTC timestamps. The machine-readable contract is
`packages/types/openapi.json`; the web app's types are generated from it and CI fails on drift.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /health/live`, `GET /health/ready` | none | readiness checks DB (and Redis, degraded-tolerant) |
| `GET /auth/config` | none | enabled login methods |
| `POST /auth/login`, `POST /auth/register` | none | sets HttpOnly session cookie; returns user + CSRF token |
| `GET /auth/session` | cookie | 401 when absent/expired |
| `POST /auth/logout` | cookie + CSRF | revokes session |
| `GET /auth/oidc/login`, `/auth/oidc/callback` | none | optional SSO (PKCE) |
| `GET /companies?query&limit` | viewer | ranked: exact ticker, ticker prefix, name prefix, contains |
| `GET /companies/{ticker}` | viewer | profile + listings + `sources[]` + per-domain availability; lazily refreshes SEC submissions (TTL 7d) |
| `GET /market-data/{ticker}/bars?from&to&interval=1d` | viewer | bars with `fetch_id`, `summary` (last close, change), `quality` (fresh/cached/stale, warnings), `sources[]` |
| `GET/POST /watchlists`, `DELETE /watchlists/{id}` | viewer / analyst | owner-scoped (others' IDs return 404) |
| `POST /watchlists/{id}/items`, `DELETE /watchlists/{id}/items/{ticker}` | analyst | |
| `GET /system/status` | viewer | provider configuration + last success/error, directory stats |
| `GET /admin/users`, `PATCH /admin/users/{id}` | admin | role/active changes revoke sessions; last admin protected |
| `POST /admin/ingestion/sec-directory` | admin | runs the SEC directory sync |
| `GET /admin/provider-fetches`, `GET /admin/audit-events` | admin | observability |

Error codes are stable strings (`authentication_required`, `csrf_token_invalid`, `rate_limited`,
`company_not_found`, `provider_not_configured`, `provider_credentials_missing`, …).

## 5. Data-provider interfaces

`app/providers/base.py` defines `FetchMeta` (provenance), `FetchResult[T]` (data + meta +
rejected count), `ProviderError` (stable `code`, retryable flag), and `record_fetch()`.

- **Market data port** — `MarketDataProvider.get_daily_bars(ticker, start, end) -> FetchResult[list[DailyBar]]`
  plus `ProviderInfo` (name, license note, credential env var). `registry.market_data_status()`
  explains exactly what configuration is missing. Implemented: **Tiingo** (`TIINGO_API_KEY`).
  Tokens are sent as headers and never recorded.
- **SEC EDGAR** — `SecEdgarClient.fetch_directory()`, `fetch_submissions(cik)` (profile +
  recent filing index in one request), `fetch_submission_page(cik, name)` (older filings),
  `fetch_company_facts(cik, …)`, `fetch_filing_document(cik, accession, name)`; enforces SEC
  fair access (≤10 req/s, identifying `SEC_USER_AGENT`), retries 429/5xx with backoff, and
  validates accession numbers and document names before building archive URLs.
- **Embeddings** — `EmbeddingProvider` (`name`, `dimensions`, `embed_documents`, `embed_query`).
  Implemented: `FastEmbedProvider`, an open model (BAAI/bge-small-en-v1.5) run locally through
  ONNX Runtime; `EMBEDDING_PROVIDER=none` leaves search to full-text. Vectors record their model
  and only the active model's vectors are searched.
- **Caching rule for adjusted prices**: a cache hit requires every bar in the range to come from
  one fresh retrieval covering the range, so provider-adjusted values share one adjustment basis.
- Future ports follow the same shape: `FundamentalsProvider`, `NewsProvider`, `MacroProvider`
  (FRED), `LLMProvider` (structured output, tool calling, embeddings), `BrokerAdapter`
  (`get_account`, `get_positions`, `get_quotes`, `preview_order`, `submit_order`, `cancel_order`,
  `get_order_status`; only `PaperBrokerAdapter` will be implemented).

## 6. ML pipeline (Phase 6+ design)

1. **Ingest** with both effective/period dates and `available_at` (publication) timestamps.
2. **Feature store**: immutable, versioned snapshots keyed by (security, as_of); a feature may
   only read rows with `available_at <= as_of`. Leakage tests assert this for every feature.
3. **Universe**: point-in-time membership including delisted securities.
4. **Targets**: forward returns over 1d/5d/20d/3m/6m/12m computed from corporate-action-adjusted
   prices; direction, volatility, drawdown, relative-performance labels.
5. **Models**: logistic/linear baselines first, then gradient boosting (LightGBM/XGBoost); deep
   models only with evidence. Walk-forward splits with embargo; never shuffled.
6. **Evaluation**: MAE/RMSE/rank-IC for regression; Brier, log loss, calibration curves for
   probabilities; Sharpe/Sortino/drawdown/turnover for strategies; segment by sector, regime,
   horizon, event type.
7. **Calibration**: Platt vs isotonic on a time-separated window; chosen calibrator is versioned.
8. **Serving**: prediction + uncertainty + top SHAP drivers + data timestamp + model version,
   always labelled as model output; every prediction is journaled and later scored.

## 7. Security model

- **Authentication**: argon2id passwords (≥12 chars), generic failure messages, timing-equalised
  unknown-user path, lockout after 10 failures (15 min), per-IP and per-email rate limits
  (Redis fixed window, memory fallback). Optional OIDC (PKCE; links to existing accounts only on
  `email_verified`).
- **Sessions**: opaque 256-bit token in an HttpOnly, SameSite=Lax cookie (Secure in production);
  only its SHA-256 is stored; 12 h absolute / 60 min idle expiry; server-side revocation;
  role or status changes revoke all sessions.
- **CSRF**: synchronizer token required on every unsafe request, plus an Origin allow-list check.
- **RBAC**: viewer (read research data) < analyst (personal workspace) < admin (users, ingestion,
  audit). Ownership is part of every workspace query.
- **Transport/browser**: same-origin API via rewrite (no CORS by default); nosniff, frame-deny,
  referrer policy, no-store API responses; `poweredByHeader` off.
- **Secrets** only from environment; production start-up fails on unsafe configuration
  (non-HTTPS frontend, dev DB password, weak `AUTH_SECRET` with OIDC, no login method).
- **Audit**: logins (success/failure/denied), registration, logout, admin changes, ingestion.
- **Logging** is structured JSON with request IDs and automatic redaction of secret-like keys.

## 8. Testing strategy

| Layer | Tooling | Scope |
|---|---|---|
| Formula tests | pytest | `analytics/` exact Decimal results, edge cases |
| Provider tests | pytest + `httpx.MockTransport` | parsing, validation/rejection, error mapping, credential hygiene |
| Integration | pytest + real PostgreSQL via Alembic | auth lifecycle, CSRF, rate limits, lockout, RBAC, ownership, SEC sync semantics, market-data caching/fallback, UTC timestamps |
| Migrations | Alembic | upgrade → downgrade → upgrade, `alembic check` for model drift |
| Contract | OpenAPI export + generated TS types | CI fails if either drifts |
| Web unit | Vitest | formatters, API client, redirect safety, chart transforms, module registry |
| Static | ruff, mypy, ESLint, `tsc` | CI gates |
| Later | leakage tests, walk-forward harness, calibration reports, backtest bias tests | Phases 6–7 |
