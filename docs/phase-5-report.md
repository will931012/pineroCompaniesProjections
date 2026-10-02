# Phase 5 Report — Valuation

Completed 2026-10-02.

## Decisions taken with the user

- **Discount rate: CAPM from sources.**
  - Risk-free rate: the 10-year Treasury par yield from treasury.gov.
  - Beta: weekly returns against SPY, Blume-adjusted. When price history is missing, 1.0 is
    used and a warning says so.
  - Equity risk premium: 5%, editable.
  - Cost of debt: interest expense ÷ debt.
  - Weights: market values, falling back to book equity with a warning.
- **DCF: ten years in two stages.**
  - Years 1–5 use recent growth and margin.
  - Years 6–10 fade linearly to terminal growth and a long-run margin.
  - The terminal value is a Gordon growth value, with terminal growth defaulting to 2.5% and
    capped at the risk-free rate.
- **Scenarios: shift the key drivers.**
  - Bear: growth −3 pts, margin −2 pts, discount rate +1 pt, terminal growth −0.5 pt.
  - Bull: the mirror image.
  - Every shift is editable and stored with a saved run.

## Implemented

**Valuation engine (`services/api/app/analytics/valuation.py`)**

The engine is pure functions over floats with no I/O. `FORMULA_VERSION` is `2026.10-1` and is
stored with every result.

- **DCF (free cash flow to the firm).**
  - Each year: NOPAT = operating income × (1 − tax), with losses not taxed.
  - Reinvestment = change in revenue ÷ sales-to-capital.
  - Terminal cash flow = NOPAT₁₁ × (1 − gT ÷ RONIC). RONIC (return on new invested capital)
    defaults to the discount rate, so growth after year 10 adds no value.
  - Equity = EV − debt + cash. Mid-year discounting is optional.
- **Residual income (RIM)**, for banks and insurers.
  - Value = book value + the present value of (ROE − ke) × opening book value.
  - Book value grows by retained earnings.
  - ROE fades to a long-run ROE, which defaults to ke (no excess return after year 10).
- **Dividend discount (DDM).** Dividends grow at g₁ for years 1–5, fade to gT, then a Gordon
  terminal value.
- **WACC / CAPM.**
  - ke = rf + β × ERP.
  - WACC = E/(D+E) × ke + D/(D+E) × kd × (1 − t).
- **Guard rails.** The discount rate must exceed terminal growth by at least 0.5 pt, or
  `ValuationError` explains why.
- **Sensitivity grids.**
  - All models: discount rate × terminal growth.
  - DCF: growth × margin.
  - RIM: ROE × discount rate.
  - Cells that cannot be valued are empty, never clamped.

**Default assumptions with sources (`app/valuation/service.py`)**

Every default records where it came from. Each lookup tries the trailing twelve months first
and falls back to the latest fiscal year on its own. Ratios (margin, ROE, payout) take every
input from the same period, and the source names that period, e.g. "operating margin,
TTM Q3 FY2026".

Each default has a stated fallback, and every fallback adds a warning:

| Default | Source | Bounds / fallback |
|---|---|---|
| Revenue growth | 3-year CAGR | −10…30%; year-on-year growth if there's no 3-year history |
| Operating margin | current period | none |
| Long-run margin | 5-year average | none |
| Sales-to-capital | revenue ÷ (equity + debt − cash) | 0.5–5; 1.5 if not computable |
| Tax rate | 3-year effective rate | 10–30%; 21% statutory if not computable |
| Cost of debt | interest expense ÷ debt | rf…rf + 8%; rf + 1.5% if not computable |
| Dividend growth | 5-year CAGR of dividends per share | −5…12%; 3% if not computable |
| Debt and cash | standard XBRL tags | 0, with a warning, when the company doesn't report them there |

Other rules:
- **Shares.** Taken from the market-cap share count. Without a stored price, the count must be
  current for the latest reported period. If there is none, every model is unavailable and the
  reason is stated. A share count is never guessed.
- **Recommended model.** Residual income for SIC 6000–6799 (banks, insurers, financial holding
  companies); DCF otherwise.

**Market inputs**
- **`app/providers/treasury.py`**: the daily par yield curve CSV from home.treasury.gov
  (public domain; robots.txt allows all).
  - Stored in `market_rates` with a provenance row.
  - Refreshed when older than 12 hours. One request per refresh.
- **Beta**: weekly log returns (every fifth shared trading day) over up to two years against
  SPY, at least 52 weeks, Blume-adjusted (0.67 × raw + 0.33), bounded to 0.3–3.

**Relative valuation (`app/valuation/relative.py`)**
- Four multiples: P/E, EV/EBITDA, P/S and P/B.
- Each is compared against:
  - the peer median (Phase 2 peer selection, now in `app/fundamentals/peers.py`);
  - the industry median (SIC major group);
  - the company's own 5-year median, using the close nearest each fiscal year end.
- Medians use positive multiples only.
- The implied value applies the peer median to the company's own latest figure.

**API (`app/valuation/routes.py`, migration `0006`)**
- `GET /companies/{t}/valuation/defaults`: inputs per model, CAPM inputs, default scenario
  shifts, sources, warnings, and the reasons any model is unavailable.
- `POST /companies/{t}/valuation/compute`: bear/base/bull values, upside vs the stored close,
  the rates used, and the sensitivity grids. Nothing is saved.
- `POST/GET /companies/{t}/valuation/runs`, `GET/DELETE /valuation/runs/{id}`: saved runs
  keep the exact request, its sources (edited inputs are marked as edited), the results, the
  price at the time and the formula version.
  - Runs are private to their owner; another user gets a 404.
- `GET /companies/{t}/valuation/relative`.
- Invalid assumptions return `422 invalid_assumptions` with the reason.
- Tables:
  - `market_rates`: unique on (series, tenor, date), with `fetch_id`;
  - `valuation_runs`: UUID, owner, company, model, assumptions/sources/results as JSONB,
    price, formula version.

**Web: Valuation tab**
- **Model picker.** Unavailable models are disabled, with the reason on hover; the recommended
  model is starred.
- **Warnings** from the defaults are listed.
- **Scenario cards** show bear, base and bull values with upside against the last close.
- **Value bridge**: EV → equity → per share for DCF, book value + residual income for RIM.
- **Rates** used: cost of equity, after-tax cost of debt, equity weight, WACC.
- **Assumptions form.**
  - Rates are entered in %, amounts in millions.
  - Every field shows its source, or "Edited · default X" once changed.
  - Invalid fields are flagged and nothing is recomputed until they're fixed.
  - A Reset button restores the defaults.
  - Results recompute 300 ms after the last edit.
- **Ten-year projection table.**
- **Sensitivity heatmaps.**
  - With a price, colours diverge around it: coral below, green above, gray within ±10%.
  - Without a price, one hue in thirds of the grid's range.
  - The base cell is outlined, each cell shows the value and its upside, and a key is shown.
  - The colour steps passed the dataviz palette validator, and dark text stays at least
    4.6:1 on every fill.
- **Saved runs**: save, load (reproduces the saved inputs), delete.
- **Relative valuation panel.**

**Fixed along the way**
- The company profile still listed SEC filings and News as "planned" after Phases 3–4, and
  valuation as Phase 5. All three now report as available when the company has a CIK.

## Verification (run locally on 2026-10-02)

| Check | Result |
|---|---|
| API tests (pytest, PostgreSQL 18 + pgvector) | 217 passed (128 unit, 89 integration); Phase 5 added 25 (17 unit, 8 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (111 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 51 passed (5 new for the valuation form and heatmap tones) |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK |
| `npm audit` | 0 vulnerabilities |
| Browser end to end (headless Edge, desktop + 390px) | AAPL DCF: editing growth 1.81% → 8% moved the base value from $115.38 to $159.36 and marked the field edited. Non-numeric beta was rejected. Save → reset → load reproduced the saved $153.37; delete removed it. JPM opened on residual income with DCF disabled. BRK-B showed its reason. No page errors, no horizontal overflow. |

**Unit tests check values computed by hand:**
- WACC 8.9%: rf 4%, β 1.2, ERP 5%, kd 6%, tax 25%, E 800, D 200.
- A zero-growth DCF equals its perpetuity: EV 1,500 → equity 1,350 → 135 per share.
- With RONIC equal to WACC, the terminal value equals NOPAT₁₁ ÷ r.
- Residual income equals book value when ROE = ke, and equals 100 + 5/10% = 150 for a
  permanent 5-point excess return.
- A DDM with constant growth equals Gordon: 2 × 1.03 ÷ 5% = 41.20.
- A synthetic beta of 1.5 adjusts to 1.335.

**Integration tests** run the API against the Phase 2 companyfacts fixture with a mocked
Treasury file. They check that:
- defaults match hand calculations, including that revenue growth 1200/990 − 1 is labelled
  "FY2024" because operating income is only reported annually;
- the Treasury fallback works;
- financial companies default to residual income;
- the compute endpoint equals the engine;
- the discount rate override works;
- invalid assumptions return 422;
- saved runs are isolated by owner;
- with mock prices, the upside and the peer-median P/E (10.4 → implied $26.00) are correct.

**Observed on real SEC data**

10-year Treasury par yield on 2026-10-01: 5.24%.

| Company | Model | Bear | Base | Bull | Notes |
|---|---|---|---|---|---|
| AAPL | DCF | $81.60 | $115.38 | $167.41 | WACC 8.2% with book weights; 3-year revenue CAGR 1.8%; terminal value 48% of EV |
| MSFT | DCF | $249.95 | $342.25 | $480.78 | WACC 9.9% |
| NVDA | DCF | $185.40 | $256.37 | $360.73 | growth capped at 30%; long-run margin 46% vs current 65% |
| JPM | RIM (recommended) | $181.57 | $213.84 | $251.29 | DCF unavailable: no revenue/operating income line for a bank |
| BRK-B | none | — | — | — | Berkshire reports shares and EPS only under its own tags (standard tags end in 2015), and its borrowings aren't in the standard debt tags. All models are unavailable with that reason, and the missing debt is flagged. |

Not verified here:
- No price-based input could be checked on live data, because Tiingo isn't configured
  locally. That covers beta, market-value weights, upside and relative multiples. Every
  company above therefore shows the "beta 1.0" and "book weights" warnings, and the relative
  panel shows no values. The price paths are covered by integration tests with mock prices.

## Deploying this phase (Railway)

Since 2026-10-02 the Railway services have their root directory and config path set, so a
deploy of `api` runs `alembic upgrade head` (migrations `0003`–`0006`) before starting. The
Phase 4 worker still needs its own service; see [deployment.md](deployment.md).

Valuation needs:
- no new variables;
- outbound HTTPS to `home.treasury.gov`;
- for market-value weights, beta and upside, `TIINGO_API_KEY` with
  `MARKET_DATA_PROVIDER=tiingo`, plus price history for the company and SPY.

## Known limitations and technical debt

1. **Sum-of-the-parts (SOTP) is deferred.** It needs segment revenue and operating income;
   SEC companyfacts doesn't include segment members, so this needs the filings' XBRL
   instance documents.
2. **Without prices, the cost of capital is weaker.** Beta is 1.0 and weights are book values
   (both flagged). This mostly affects buyback-heavy companies, whose book equity is small:
   Apple's equity weight is 57% on book, far below its market-value weight, which lowers
   its WACC.
3. **Residual income understates companies with tiny book equity.** ROE is bounded at 40% and
   fades to ke, so Apple's RIM ($35.77) is not meaningful. DCF is recommended for it; the
   bound is visible and editable.
4. **FCFF uses operating income as reported.** No adjustments are made for leases,
   stock-based compensation or R&D capitalisation; these are listed for a later formula
   version.
5. **Issuer-specific XBRL tags are not read.** Companies that report shares or debt only under
   their own tags (Berkshire) cannot be valued per share. The values are not guessed.
6. **USD only.** Foreign filers reporting in other currencies are not converted.
7. **Relative valuation reads the latest stored snapshot of each peer.** A peer shows blanks
   until its financials and prices are loaded.
8. **Open items from earlier phases:** market cap after splits, bank line items, CSP, password
   reset/MFA, the README note for `AUTH_DISABLED`, and the Railway worker service.
