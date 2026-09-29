# Implementation Roadmap

A phase is complete only when its contracts, tests, migrations, and operational checks pass.
No placeholder data is added to make a module look finished; unbuilt modules say so in the UI.

## Phase 1 — Foundation ✔ (2026-09-29)

Auth (password + optional OIDC), RBAC, sessions, CSRF, rate limiting, audit log; provenance
model; SEC-sourced company directory and profiles; market-data port with Tiingo adapter and
persisted daily bars; watchlists; admin console; web shell with all navigation; structured
logging, metrics, health; Docker/CI. See [phase-1-report.md](phase-1-report.md).

## Phase 2 — Fundamentals

- SEC XBRL `companyfacts` adapter (no key needed) → `financial_statements` with period end,
  filing accession, **filed/available date**, unit, and original concept.
- Normalisation map from us-gaap concepts to a canonical statement model; unmapped concepts kept.
- Deterministic metrics in `analytics/fundamentals.py` (growth, margins, ROE/ROA/ROIC, liquidity,
  leverage, coverage, FCF, dilution, SBC, buyback yield) with formula test vectors.
- Shares outstanding → market cap and enterprise value on the company header.
- Financials tab, historical charts, peer comparison (SIC-based peers + user-selected).
- Screener v1 on fundamentals.

## Phase 3 — SEC filings and retrieval

Filing index from submissions, filing viewer, section extraction (10-K/10-Q items, 8-K items),
section-level diffs between periods, Form 4 insider transactions, 13F holdings; pgvector
embeddings behind a provider-neutral LLM/embedding port; retrieval constrained to cited sections.

## Phase 4 — News, events, alerts

Licensed/RSS adapters with license tracking; entity linking; event taxonomy; materiality and
novelty evidence (not naive sentiment); event graph; earnings events; alert rules + worker
(Redis-backed job queue introduced here).

## Phase 5 — Valuation

DCF (explicit, user-editable assumptions; bear/base/bull; sensitivity grids), relative valuation
vs history/sector/peers, DDM/RIM/SOTP where appropriate; assumptions stored with every result.

## Phase 6 — Quantitative research

Macro data (FRED), point-in-time feature store, factor scores, baseline and gradient-boosted
models, calibration, SHAP, prediction journal, regime detection, leakage tests.

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
