# Implementation Roadmap

A phase is complete only when its contracts, tests, migrations, and operational checks pass.
No placeholder data is added to make a module look finished; unbuilt modules say so in the UI.

## Phase 1 — Foundation ✔ (2026-09-29)

Auth (password + optional OIDC), RBAC, sessions, CSRF, rate limiting, audit log; provenance
model; SEC-sourced company directory and profiles; market-data port with Tiingo adapter and
persisted daily bars; watchlists; admin console; web shell with all navigation; structured
logging, metrics, health; Docker/CI. See [phase-1-report.md](phase-1-report.md).

## Phase 2 — Fundamentals ✔ (2026-09-30)

SEC XBRL `companyfacts` ingestion into point-in-time `financial_facts` (accession, form,
filed date, unit, original concept); canonical statements rebuilt as of any date, with derived
quarters, in-progress fiscal years, and stock-split restatement; deterministic metrics with
formula test vectors; market cap and EV on the company header; Financials and Peers tabs with
historical charts; screener v1. Only tracked concepts are stored; mapping more (for example
bank line items) is a follow-up. See [phase-2-report.md](phase-2-report.md).

## Phase 3 — SEC filings and retrieval ✔ (2026-09-30)

Filing index from SEC submissions (with older history on request); in-app filing viewer;
10-K/10-Q/8-K item extraction; paragraph and word-level diffs against the prior filing of the
same form; Form 4 insider transactions; hybrid search over filing passages (PostgreSQL
full-text + a local open embedding model in pgvector, behind a provider port), where every hit
cites its filing, section, and character span. See [phase-3-report.md](phase-3-report.md).

13F institutional holdings were deferred to Phase 6: 13F lists positions by CUSIP, and SEC's
ticker file has no CUSIP mapping, so linking holdings to companies needs a mapping source.

## Phase 4 — News, events, alerts

Licensed/RSS adapters with license tracking; entity linking; event taxonomy; materiality and
novelty evidence (not naive sentiment); event graph; earnings events; alert rules + worker
(Redis-backed job queue introduced here).

## Phase 5 — Valuation

DCF (explicit, user-editable assumptions; bear/base/bull; sensitivity grids), relative valuation
vs history/sector/peers, DDM/RIM/SOTP where appropriate; assumptions stored with every result.

## Phase 6 — Quantitative research

Macro data (FRED), point-in-time feature store, factor scores, baseline and gradient-boosted
models, calibration, SHAP, prediction journal, regime detection, leakage tests. 13F
institutional ownership (deferred from Phase 3), with a CUSIP→ticker mapping source.

## Phase 7 — Backtesting

Walk-forward simulation with costs, spread, slippage, liquidity, corporate actions, delisted
securities; benchmark-relative analytics.

## Phase 8 — AI research and thesis memory

Research agent orchestrating structured data and retrieval; reports separating fact /
calculation / model output / interpretation with citations; versioned theses; research chat.

## Phase 9 — Portfolio and risk

Exposures, correlation, VaR/CVaR, stress scenarios, configurable risk limits, emergency controls.

## Phase 10 — Paper trading

`PaperBrokerAdapter`, simulated fills/costs, full order history, pre-trade risk checks.
Real-money execution remains out of scope.
