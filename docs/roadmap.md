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

## Phase 4 — News, events, alerts ✔ (2026-09-30)

News from the GDELT open index (headlines and links only, within its published rules) and SEC
8-K events; company linking with recorded method and confidence; a deterministic event
taxonomy with evidence; novelty and story clustering with the local embedding model; earnings
releases with their EX-99 press releases; alert rules delivered by email (Resend) from a
Postgres-backed background worker. Yahoo Finance was dropped because its robots.txt forbids
automated access. Upcoming earnings dates need a calendar source and are not included. See
[phase-4-report.md](phase-4-report.md).

## Phase 5 — Valuation ✔ (2026-10-02)

Ten-year, two-stage FCFF DCF with CAPM/WACC built from sourced inputs (Treasury 10-year,
beta vs SPY, interest ÷ debt); residual income (recommended for financials) and dividend
discount models; bear/base/bull driver shifts; sensitivity grids; relative valuation against
peer, industry and 5-year medians; saved runs keep their inputs, sources, results and formula
version. Sum-of-the-parts is deferred: segment data is not in SEC companyfacts. See
[phase-5-report.md](phase-5-report.md).

## Phase 6 — Quantitative research ✔ (2026-10-03)

Point-in-time universe (200 largest by SEC revenue, with survivorship measured); FRED macro
with vintages; feature store with leakage tests; factor scores; rule-based regimes; LightGBM
direction and quantile models with walk-forward evaluation, time-separated calibration,
SHAP, a prediction journal, and a validation rule (no model passes yet, and the app says so);
13F ownership with OpenFIGI; Markets, Bitcoin, and Models pages; Technicals, Quant, and
Ownership tabs. See [phase-6-report.md](phase-6-report.md).

## Phase 7 — Backtesting ✔ (2026-10-05)

Long-only factor-rule backtests on the point-in-time universe: daily simulation with splits
and dividends from raw prices, next-open execution, estimated commission, high/low spread,
square-root impact and a volume cap; comparisons with SPY, an equal-weight universe, and the
same strategy before costs; probabilistic and deflated Sharpe ratios on the return above SPY,
counting the user's own tries. See [phase-7-report.md](phase-7-report.md).

## Phase 8 — AI research and thesis memory

Research agent orchestrating structured data and retrieval; reports separating fact /
calculation / model output / interpretation with citations; versioned theses; research chat.

## Phase 9 — Portfolio and risk

Exposures, correlation, VaR/CVaR, stress scenarios, configurable risk limits, emergency controls.

## Phase 10 — Paper trading

`PaperBrokerAdapter`, simulated fills/costs, full order history, pre-trade risk checks.
Real-money execution remains out of scope.
