# Phase 7 Report — Backtesting

Completed 2026-10-05.

## Decisions taken with the user

- **Signals:** factor rules only. Rank the point-in-time universe by one Phase 6 factor or a
  weighted blend (value, quality, momentum, low volatility, growth, size), and hold the top N
  or top fraction.
- **Long only.**
- **Costs estimated from data:**
  - commission per share;
  - bid-ask spread from daily highs and lows (Corwin–Schultz);
  - square-root market impact;
  - a cap on participation in daily volume.

  Every parameter is editable and shown.

## Implemented

**Engine (`app/analytics/backtest.py`, version `b1`; pure)**
- Each trading day, in order:
  1. Splits change share counts.
  2. Dividends are credited on shares held at the start of the day (ex-date).
  3. On the first trading day after a signal date, orders execute at the open: sells first,
     then buys sized so that value plus estimated costs fit the cash.
  4. Marks at the close.
- Prices are raw. Splits and dividends come from Tiingo's factors, the same rebuild that
  matched adjusted closes to 7e-8 in Phase 6, so corporate actions need no adjusted series.
- **Costs per order, using only data from before the execution day:**
  - commission: max(minimum, per-share × shares);
  - spread: half the Corwin–Schultz spread over the previous 21 days, with the paper's
    overnight-gap adjustment, a fallback when not estimable, and a cap;
  - impact: coefficient × daily volatility × √(order ÷ average dollar volume) × order.
- **Liquidity:** an order is cut to at most the participation limit (default 10%) of 20-day
  average dollar volume.
- **Stale positions:** a holding with no trades for 10+ days is exited at its last price at
  the next rebalance, and flagged.
- **Speed:** trailing statistics come from running sums over per-security arrays, so each
  lookup is O(1). Price panels are cached in-process until a new price fetch arrives. A
  14-year run takes 4–13 seconds once warm and about 20 seconds cold.

**Performance (`app/analytics/performance.py`)**
- CAGR, volatility, Sharpe and Sortino against the 3-month T-bill (FRED DTB3, as published).
- Largest fall, with its dates, and the longest time below a high.
- Beta and alpha (CAPM regression on excess returns), tracking error and information ratio
  against SPY.
- Calendar-year and monthly returns, months beating SPY, rolling 12-month excess.
- **Probabilistic Sharpe ratio** (Bailey & López de Prado, 2012), computed on the return
  above SPY: the probability that the strategy truly beats the index given the sample's length,
  skew and kurtosis.
- **Deflated Sharpe ratio** (2014): the same, against the best of N unskilled tries. N counts
  the user's earlier backtests over overlapping periods, and the variance comes from their
  results.

**Strategy and comparisons (`app/backtesting/service.py`)**
- On each month end (or quarter end), the eligible members are those of the 200 ranked by
  revenue known then that have a year of prices. They are ranked by a weighted blend of their
  stored factor z-scores. At least half the blend's weight must be present.
- **Weighting:** equal, by rank, or inverse 3-month volatility, with a per-stock cap
  (excess redistributed).
- **Every run also simulates:**
  - the same strategy before costs;
  - an equal-weight portfolio of all eligible members, rebalanced on the same dates with the
    same costs, which separates the factor's effect from equal-weighting;
  - SPY total return with dividends reinvested.
- **Stored per run:** the exact spec, statistics, series, annual and monthly returns,
  turnover, costs by type, latest holdings, coverage per date, warnings and assumptions. Runs
  are private to their owner.

**API and web**
- Endpoints: `GET /backtests/options`, `POST /backtests` (runs and stores), `GET /backtests`,
  `GET/DELETE /backtests/{id}`. Migration `0008`.
- **Backtests page:**
  - form: factor weight sliders, selection, weighting, cap, rebalance, dates, capital, and
    cost assumptions with explanations;
  - list of saved runs;
  - result view:
    - headline cards (after costs, before costs, SPY, equal weight);
    - growth chart (log scale) and charts of drawdowns and rolling 12-month excess;
    - statistics table, annual returns, turnover and costs;
    - universe coverage, latest holdings, assumptions and warnings.

## Results on real data

Top 20, equal weight, monthly, 10% cap, $1M, February 2012 to October 2026, default costs:

| Strategy | CAGR after costs | Before costs | Volatility | Sharpe | Largest fall | Info ratio vs SPY | Costs / yr | P(beats SPY) | Deflated |
|---|---|---|---|---|---|---|---|---|---|
| Value | 17.2% | 19.3% | 25.6% | 0.69 | −57.2% | 0.24 | 186 bps | 0.82 | (1st run) |
| Quality | 14.9% | 15.9% | 16.0% | 0.85 | −31.9% | 0.01 | 87 bps | 0.52 | 0.52 |
| Momentum | 16.2% | 19.2% | 21.5% | 0.73 | −43.0% | 0.17 | 266 bps | 0.74 | 0.55 |
| Low volatility | 8.7% | 10.1% | 11.8% | 0.62 | −27.4% | −0.49 | 101 bps | 0.03 | 0.01 |
| Growth | 13.3% | 14.7% | 20.6% | 0.63 | −38.4% | −0.05 | 127 bps | 0.42 | 0.04 |
| Size (largest first) | 14.8% | 15.4% | 16.3% | 0.83 | −29.4% | 0.00 | 52 bps | 0.50 | 0.08 |
| Quality + value + momentum | 18.2% | 20.9% | 20.8% | 0.83 | −42.6% | 0.33 | 240 bps | 0.89 | 0.46 |
| Equal-weight universe | 14.7% | | 17.1% | 0.79 | −40.7% | | | | |
| SPY | 14.7% | | 16.5% | 0.82 | −33.7% | | | | |

- **No variant beats SPY with confidence after allowing for the search.** The three-factor
  blend has an 89% probability of truly beating SPY taken alone, but 46% once the seven
  variants tried here are counted. Value earned more but with a −57% fall and a lower Sharpe
  ratio than SPY.
- **Costs matter.** Momentum and the blend lose 2.6–3.1 percentage points a year to costs.
  Most of that is the spread estimate (below).
- **The equal-weight universe matched SPY (14.7%),** so equal-weighting alone added nothing
  over this period.
- **Coverage:** 145 of 200 members could be held in January 2012, rising to 191 of 200 in
  September 2026. Early years lean toward survivors.

## Verification (run locally on 2026-10-05)

| Check | Result |
|---|---|
| API tests | 270 passed (168 unit, 102 integration); Phase 7 added 14 (10 unit, 4 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (136 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 57 passed (3 new) |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK (`/backtests`) |
| `npm audit --omit=dev` | 0 vulnerabilities (the dev-only `braces` advisory from Phase 6 remains in the lint tooling) |
| Browser end to end (headless Edge, desktop + 390px) | A backtest set through the form (quality 1 + momentum 0.5) ran in 20 s cold, rendered cards, charts, statistics, holdings and the deflated result (8 tries: 28%), and was deleted; no page errors; no horizontal overflow after a heading-wrap fix |

**Unit tests check the engine against hand calculations:**
- Corwin–Schultz against its closed form;
- each cost formula, the minimum commission and the fallback spread;
- buy and hold through a 2-for-1 split and a $1 dividend (value = 10,000 + 200 × $1);
- execution at the next open even when the signal day's close spikes;
- the participation cap ($1,000 on a $10,000-a-day stock);
- sells before buys, with cash never negative;
- the stale-position exit at the last price;
- weight capping;
- CAGR and drawdown;
- that the deflated probability is below the plain one.

**Integration tests** use a synthetic 25-stock universe with known scores. They check that the
top 5 are held at 20% each, that costs are charged, that trades start the day after each
signal, that runs are private and counted as tries, and that invalid or too-short specs are
rejected or fail with a reason.

## Known limitations and technical debt

1. **The spread estimator overstates large-cap spreads.** Corwin–Schultz reads about 40 bps
   for Apple and 22 bps for the Dow ETF, against quoted spreads of a few basis points.
   Abdi–Ranaldo was tried and is unstable (often 0). Costs here are therefore conservative;
   the spread cap is the lever, and before-cost results are always shown.
2. **Survivorship.** Delisted companies cannot be held (no prices), which flatters early
   years. Coverage is reported per date.
3. **Fills:** fractional shares, execution at the open with no intraday price path, and no
   cash interest.
4. **Factor scores are monthly,** so signals cannot be more frequent than monthly.
5. **No sector or turnover constraints yet,** and no portfolio from a fixed list. The user
   chose factor rules only.
6. **Backtests run inside the request** (about 4–20 s). The worker queue is available if
   they grow.
7. **Open from earlier phases:** Tiingo's internal-use licence, the Railway worker service,
   CSP, and password reset/MFA.
