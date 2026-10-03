# Phase 6 Report — Quantitative research

Completed 2026-10-03.

## Decisions taken with the user

- **Scope.** All of Phase 6 at once (the user declined splitting it in two).
- **Model universe.** About 200 large caps, ranked point in time by SEC-reported revenue, plus
  SPY. Delisted companies are kept where SEC and Tiingo still have data; the remaining
  survivorship bias is measured.
- **Macro.** FRED API (key provided by the user, set locally and on Railway `api`).
- **13F.** CUSIP → ticker through OpenFIGI.
- **Bitcoin** (added mid-phase): Bitcoin only, on its own page, with price analytics and a
  halving-cycle view; no on-chain data and no Bitcoin model.

## Implemented

**Data sources (each request recorded in `provider_fetches`)**

| Source | Used for | Rules respected |
|---|---|---|
| SEC XBRL frames | universe candidates (top 300 by revenue in any year 2011–2025) | SEC fair access, ≤8 requests/s |
| SEC companyfacts | point-in-time revenue and fundamentals for 533 more registrants | same |
| SEC Form 13F data sets | quarterly holdings (two quarters, ~100 MB each) | `/files/` allowed by robots.txt |
| Tiingo end-of-day | 310 listings since 2010 | free plan: 50 requests/hour, 1,000/day, 500 symbols/month, internal use only |
| Tiingo crypto | Bitcoin daily since 2011-08-19 | same budget |
| FRED / ALFRED | 12 macro series with vintages | 120 requests/minute; attribution shown |
| U.S. Treasury | par yield curve 2011–2026 | public domain |
| OpenFIGI | 2,112 CUSIPs mapped, 1,508 linked to companies | keyless: 25 requests/min, 10 per request |

**Point-in-time universe (`app/quant/universe.py`)**
- Each month end from January 2012, candidates are ranked by their latest fiscal-year revenue
  whose filing was public by that date. Restatements count from when they were filed.
- **Successor registrants.** Six companies changed SEC registrant while keeping their stock:
  Exxon Mobil, Cigna, US Foods, Medtronic, Eaton and Liberty Global. They are linked to
  their listing when the names match after removing legal suffixes AND the successor began
  filing between 30 days before and 120 days after the predecessor's last filing. The window
  rejects unrelated reuse of a name; for example, Constellation Energy Group (acquired in 2012)
  and Constellation Energy Corp (spun off in 2022) are kept apart.
- A listing ranked twice on one date (predecessor and successor) counts once.

**Prices (`app/quant/prices.py`)**
- Total returns are rebuilt from raw closes, dividends and split factors:
  (close + dividend) × split ÷ previous close − 1. This matched Tiingo's adjusted closes
  to 7e-8 on NVDA's two splits and 24 dividends, and it does not depend on when each bar was
  fetched.
- The bulk loader shares one budget across processes by counting Tiingo's recorded fetches
  in the last hour and day, resumes from `price_coverage`, and re-queues itself hourly. The
  initial load took 9 hourly runs.
- Reference funds missing from SEC's ticker file (QQQ, IWM, the sector SPDRs, TLT, IEF) get
  listings marked "fund (reference list)", which the directory sync never deactivates.

**Macro (`app/quant/macro.py`)**
- `available_on` is the vintage start. Where ALFRED's history begins long after the
  observation, the observation date plus a stated release lag is used instead.
- Daily rate series (DGS10, DGS2, DTB3, T10Y3M, T10Y2Y) exceed FRED's limit of 2,000 vintage
  dates per request. They are not revised after publication, so current values are stored
  with a one-day lag.
- Example of point-in-time behaviour: on 2020-04-30 the unemployment input reads 4.4%
  (March as first published). April's 14.7% was released on 8 May.

**Features, factors, regimes**
- **Feature store (`features.py`, version `f1`):** 41 inputs per company and month end.
  - Prices: momentum, volatility, beta, drawdown, distance from the 200-day average, RSI.
  - Phase 2 point-in-time fundamentals: growth, margins, returns, leverage.
  - Valuation: earnings, sales, book, EBITDA and FCF yields; dividend and buyback yields;
    size.
  - Market-wide: macro and the S&P 500 trend.
  - 29,842 snapshots for 290 companies over 177 month ends, built in about 30 minutes on 4
    processes. Every snapshot records `data_available_on`, and none is later than its date.
- **Factor scores:** value, quality, momentum, low volatility, growth and size. Each is the
  average of winsorised cross-sectional z-scores, stored with its percentile.
- **Regimes:** S&P 500 above or below its 200-day average, and stressed when its 21-day
  volatility is above the 80th percentile of the last ten years. The flags show an inverted
  10y–3m curve and NFCI > 0. Month ends from 2012: 139 calm uptrend, 12 stressed uptrend,
  22 stressed downtrend, 4 calm downtrend.

**Models (`modeling.py`, code `m2`)**
- **Target:** total return minus SPY's over the next 21 or 63 trading days.
  - Direction: LightGBM binary.
  - Range: LightGBM quantiles 10/50/90.
  - Baseline: regularised logistic regression on the same inputs.
- **Inputs:** the 31 company-level features as cross-sectional percentile ranks.
- **Walk-forward evaluation:** one fold per year from 2015. Training uses labels known at
  least 31 days before the year (purged by label end date). The calibrator is chosen
  time-separated on the last 12 months of the training window: Platt or isotonic, fitted on
  the first half and scored on the second.
- **SHAP:** LightGBM TreeSHAP. Mean absolute contribution over the last year, plus the top 5
  drivers per prediction.
- **Journal:** every prediction is stored once with its model version and data date, then
  scored after its horizon.
- **Validation rule, fixed before use:** out-of-sample AUC > 0.52, Brier below the base
  rate, and rank-IC t-stat > 2. Models that fail are labelled not validated; their output is
  hidden behind an explicit toggle on company pages but still journaled and scored.

**13F ownership (`ownership.py`)**
- Holdings are kept for the period most filings cover. Per manager, the latest original is
  used, replaced by the latest restatement if one exists, plus later new-holdings amendments.
- Options are excluded. Values before 2023-01-03 are converted from thousands.
- Positions whose value per share is more than 5× from the CUSIP's median are excluded.
  About 68,000 per quarter (4.5% of positions, 0.33% of value) fail, 97% of them off by
  exactly 1,000×: managers still reporting in thousands. Per the no-repair rule, they are
  excluded and counted.
- Only a CUSIP's U.S. composite listing links it to a company.

**API and web**
- Endpoints: `/markets/overview`, `/bitcoin`, `/companies/{t}/{technicals,factors,ownership,predictions}`,
  `/models`, `/models/{id}`, `/research/status`.
- Worker jobs: macro and Bitcoin daily; prices hourly; research daily (ranks new month ends,
  then builds missing (company, date) features, factors, regimes, predictions and outcomes);
  universe and training monthly; 13F weekly. Each step is also a CLI command:
  `python -m app.cli quant <step>`.
- New pages: Markets, Bitcoin and Models. New company tabs: Technicals, Quant and Ownership.
- Charts use one validated palette (teal, orange, blue, magenta; every validator check
  passes). The yield curve, halving cycles and calibration use SVG on numeric axes.

## Results

**The models have no demonstrated skill and are labelled not validated.** Out of sample,
2015–2026:

| | 21 days, m2 | 21 days, baseline | 63 days, m2 | 63 days, baseline |
|---|---|---|---|---|
| Predictions | 24,539 | | 24,153 | |
| AUC | 0.493 | 0.498 | 0.493 | 0.499 |
| Brier (base rate) | 0.2519 (0.2498) | 0.2514 | 0.2529 (0.2493) | 0.2532 |
| Rank IC, mean (t) | −0.013 (−1.3) | −0.006 | 0.003 (0.3) | 0.001 |
| Top − bottom decile | −0.34% | −0.32% | −0.78% | +0.18% |
| 10–90% range coverage | 67.9% (target 80%) | | 65.4% | |

- **First run (m1, kept in the database as retired).** It also used the market-wide inputs
  and reached AUC 0.502 / 0.496, with Brier worse than the base rate. Its largest SHAP drivers
  were inflation and the 10-year yield, which are the same for every company on a date, so the
  model was learning per-date levels. m2 removes them from the models; they stay in the
  snapshots for regimes and segments. This was the only change made after seeing results; both
  runs are reported.
- **The data is not empty.** Single inputs show small, known effects on 21-day excess returns:
  - buyback yield, rank IC +0.025 (t 2.8);
  - FCF yield, +0.023 (t 2.4);
  - ROIC, +0.024 (t 2.3).

  At 63 days the t-stats are higher but overstated by overlapping windows. The models do not
  turn these into out-of-sample skill.
- **No further tuning against these test years.** It would overfit the research process.
  The honest state is no validated model, while the journal keeps scoring live predictions.
- **Survivorship.** Members of the top 200 with a usable price history:

  | Month end | With prices | Without a listing |
  |---|---|---|
  | January 2012 | 146 | 52 |
  | December 2015 | 148 | 45 |
  | December 2018 | 165 | 32 |
  | December 2021 | 187 | 13 |
  | September 2026 | 193 | 5 |

  Early-period results are biased toward survivors; the Models page charts this month by
  month.

**Other real-data checks**
- **Bitcoin:** $84,200 on 2 October 2026, 32.5% below its $124,720 high of 6 October 2025.
  - 90-day correlation: S&P 500 0.39 (beta 1.23), gold 0.53, 10-year yield −0.22.
  - The 2020 cycle peaked at 8.5× after 1,402 days.
- **13F, June 2026 quarter:** Apple held by 5,872 managers, $2.69T, 63.4% of market cap.
  BlackRock holds 1.16B shares at an implied $289.36 a share.
- **Factors:** Apple ranks in the 98th percentile for quality, 77th for momentum and 5th for
  value.

## Verification (run locally on 2026-10-03)

| Check | Result |
|---|---|
| API tests | 256 passed (158 unit, 98 integration); Phase 6 added 40 (30 unit, 10 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (130 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 54 passed (3 new) |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK (`/markets`, `/bitcoin`, `/models`) |
| `npm audit` | production dependencies: 0 vulnerabilities. Dev tooling: 5 high, all one new advisory on `braces` (every version) reached through `eslint-config-next`; no patched release yet, and npm's suggested fix downgrades the lint config to Next 14, so it is not applied |
| Leakage | Features from full histories equal features from histories cut at the date (filings, prices, macro vintages) on three dates; 0 of 29,842 stored snapshots use data published after their date |
| Browser end to end (headless Edge, desktop + 390px) | Markets, Bitcoin, Models, and AAPL's Technicals/Quant/Ownership on full data: no page errors, no failed requests, no horizontal overflow |

## Deploying this phase (Railway)

See [deployment.md](deployment.md).
- Migration `0007` applies on deploy.
- The worker service is required, with the same variables as `api`, including
  `TIINGO_API_KEY` and `FRED_API_KEY`.
- The first research build takes about 7 hours of rate-limited price loading, then about
  30 minutes of features on 2 processes.
- The image adds `libgomp1`, LightGBM and scikit-learn.

## Known limitations and technical debt

1. **No validated model.** See Results. Directions to explore in a later phase, each judged
   on new data:
   - fewer, pre-specified factor inputs;
   - a continuous (rank) target;
   - a longer universe.
2. **Survivorship.** 52 of the 200 largest companies in January 2012 have no listing, so
   their prices are missing (delisted, acquired, or a successor not detected).
3. **The universe is ranked by fiscal-year revenue,** not TTM, and stops counting a company
   550 days after its last annual figure.
4. **Macro values before ALFRED's vintage history** use the first stored vintage, which may
   include later revisions; the lagged availability date is conservative.
5. **Tiingo's free plan is internal use only.** Price-derived pages must not be shown to
   other people without a commercial plan.
6. **13F:**
   - **Consistent filer errors** pass the checks. One manager filed 368,743 Berkshire shares
     under the Class A CUSIP at $264.6B in the March quarter, about 35% of that quarter's
     reported Berkshire value. A concentration rule would also flag real controlling stakes
     (Icahn/CVR, Berkshire/DaVita, Brookfield), so none is applied.
   - **CUSIP changes in reorganisations** split holdings. Exxon's old CUSIP no longer maps
     to XOM, so XOM shows 359 managers for June.
   - **Only the 100 largest holders** per quarter are stored.
7. **Quantile ranges under-cover:** 65–68% of outcomes fall inside the 10–90% range,
   against the 80% target.
8. **Open from earlier phases:** market cap after splits, bank line items, CSP, password
   reset/MFA, the README note for `AUTH_DISABLED`, and the Railway worker service.
