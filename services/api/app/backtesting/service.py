"""Factor-rule backtests over the point-in-time universe.

On each signal date (a month end, or a quarter end) the strategy ranks the universe members
that had a year of prices by a weighted blend of their stored factor scores, holds the top N
(or top fraction), and trades at the next day's open. Everything it reads is point in time:
membership and factor scores were built only from data public by the signal date.

Comparisons:
- SPY total return (dividends reinvested, no costs) — what an index investor got;
- an equal-weight portfolio of the whole eligible universe, rebalanced on the same dates with
  the same cost model — separates the factor's effect from simply equal-weighting.
"""

import math
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.analytics import performance as perf
from app.analytics.backtest import (
    CODE_VERSION,
    ZERO_COSTS,
    CostModel,
    Market,
    Rebalance,
    SimulationResult,
    simulate,
)
from app.backtesting.schemas import BacktestIn
from app.db.models import (
    Backtest,
    Company,
    DailyPrice,
    FactorScore,
    FeatureSnapshot,
    Security,
    UniverseMember,
)
from app.quant.factors import FACTOR_VERSION, FACTORS, LABELS
from app.quant.features import FEATURE_SET_VERSION
from app.quant.macro import MacroHistory
from app.quant.universe import UNIVERSE_VERSION

BENCHMARK = "SPY"
COST_LABELS = {
    "commission_per_share": ("Commission per share", "US dollars per share traded."),
    "min_commission": ("Minimum commission", "US dollars per order."),
    "spread_fallback": (
        "Spread when not estimable",
        "Full bid-ask spread used when the high/low estimator gives none.",
    ),
    "spread_cap": ("Spread cap", "Upper bound on the estimated full spread."),
    "impact_coefficient": (
        "Market impact coefficient",
        "Impact = coefficient × daily volatility × √(order ÷ average daily dollar volume).",
    ),
    "max_participation": (
        "Maximum participation",
        "Largest order as a fraction of 20-day average daily dollar volume; larger orders are cut.",
    ),
}


def signal_dates(db: Session, start: date | None, end: date | None, rebalance: str) -> list[date]:
    query = select(FactorScore.as_of).where(FactorScore.version == FACTOR_VERSION).distinct()
    dates = sorted(
        d for d in db.scalars(query) if (not start or d >= start) and (not end or d <= end)
    )
    if rebalance == "quarterly":
        dates = [d for d in dates if d.month in (3, 6, 9, 12)]
    return dates


def _cap_weights(weights: dict[int, float], cap: float) -> dict[int, float]:
    """Normalise to 1, then cap each weight and redistribute the excess pro rata."""
    total = sum(weights.values())
    w = {k: v / total for k, v in weights.items()} if total > 0 else {}
    for _ in range(50):
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        free = {k: v for k, v in w.items() if k not in over}
        for k in over:
            w[k] = cap
        free_total = sum(free.values())
        if free_total <= 0:
            break
        for k, v in free.items():
            w[k] = v + excess * v / free_total
    return w


def build_rebalances(
    db: Session, spec: BacktestIn, dates: list[date]
) -> tuple[list[Rebalance], list[Rebalance], list[dict[str, Any]]]:
    """Strategy rebalances, equal-weight universe rebalances, and per-date coverage."""
    weights: dict[str, float] = {str(f.factor): f.weight for f in spec.factors}
    members = db.execute(
        select(UniverseMember.as_of, UniverseMember.company_id, UniverseMember.security_id).where(
            UniverseMember.version == UNIVERSE_VERSION,
            UniverseMember.as_of.in_(dates),
        )
    ).all()
    eligible: dict[date, dict[int, int]] = defaultdict(dict)
    universe_count: dict[date, int] = defaultdict(int)
    for m in members:
        universe_count[m.as_of] += 1
        if m.security_id is not None:
            eligible[m.as_of][m.company_id] = m.security_id
    scores: dict[tuple[date, int], dict[str, tuple[float, float]]] = defaultdict(dict)
    for row in db.execute(
        select(
            FactorScore.as_of,
            FactorScore.company_id,
            FactorScore.factor,
            FactorScore.score,
            FactorScore.percentile,
        ).where(
            FactorScore.version == FACTOR_VERSION,
            FactorScore.as_of.in_(dates),
            FactorScore.factor.in_(list(weights)),
        )
    ):
        scores[(row.as_of, row.company_id)][row.factor] = (row.score, row.percentile)
    vol: dict[tuple[date, int], float] = {}
    if spec.weighting == "inverse_volatility":
        for row in db.execute(
            select(
                FeatureSnapshot.as_of,
                FeatureSnapshot.company_id,
                FeatureSnapshot.features["vol_3m"].astext,
            ).where(
                FeatureSnapshot.version == FEATURE_SET_VERSION, FeatureSnapshot.as_of.in_(dates)
            )
        ):
            if row[2] not in (None, "null"):
                vol[(row[0], row[1])] = float(row[2])
    strategy, equal, coverage = [], [], []
    total_weight = sum(weights.values())
    for day in dates:
        ranked = []
        for company, security in eligible[day].items():
            found = scores.get((day, company), {})
            present = sum(weights[f] for f in found)
            if present * 2 < total_weight:
                continue
            composite = sum(weights[f] * found[f][0] for f in found) / present
            ranked.append((composite, company, security))
        ranked.sort(reverse=True)
        count = (
            spec.top_n
            if spec.selection == "top_n"
            else max(1, math.floor(len(ranked) * spec.top_quantile))
        )
        chosen = ranked[:count]
        if spec.weighting == "equal":
            raw = {s: 1.0 for _, _, s in chosen}
        elif spec.weighting == "score":
            # Rank-based so weights stay positive: the best name gets the most weight.
            raw = {s: float(count - i) for i, (_, _, s) in enumerate(chosen)}
        else:
            raw = {s: 1 / vol[(day, c)] for _, c, s in chosen if vol.get((day, c), 0) > 0}
        if chosen and raw:
            strategy.append(Rebalance(day, _cap_weights(raw, spec.max_weight)))
        universe_securities = [s for _, _, s in ranked]
        if universe_securities:
            equal.append(
                Rebalance(day, {s: 1 / len(universe_securities) for s in universe_securities})
            )
        coverage.append(
            {
                "as_of": day.isoformat(),
                "universe": universe_count[day],
                "eligible": len(ranked),
                "selected": len(chosen),
            }
        )
    return strategy, equal, coverage


_MARKET_CACHE: dict[tuple[Any, ...], tuple[Market, int | None]] = {}


def load_market(
    db: Session, securities: set[int], start: date, end: date
) -> tuple[Market, int | None]:
    """Bars as arrays; cached in-process until a new price fetch is stored."""
    spy = db.scalar(select(Security.id).where(Security.ticker == BENCHMARK, Security.is_active))
    ids = sorted(securities | ({spy} if spy else set()))
    newest = db.scalar(select(func.max(DailyPrice.fetch_id)))
    key = (tuple(ids), start, end, newest)
    if key in _MARKET_CACHE:
        return _MARKET_CACHE[key]
    rows = db.execute(
        text(
            "SELECT security_id, trade_date, open::float8, high::float8, low::float8, "
            "close::float8, volume::float8, coalesce(dividend_cash, 0)::float8, "
            "coalesce(split_factor, 1)::float8 FROM daily_prices "
            "WHERE provider = 'tiingo' AND security_id = ANY(:ids) "
            "AND trade_date BETWEEN :start AND :end ORDER BY security_id, trade_date"
        ),
        {"ids": ids, "start": start - timedelta(days=60), "end": end},
    ).all()
    arrays: dict[int, tuple[list[date], np.ndarray]] = {}
    i = 0
    while i < len(rows):
        security = rows[i][0]
        j = i
        while j < len(rows) and rows[j][0] == security:
            j += 1
        block = rows[i:j]
        arrays[security] = (
            [r[1] for r in block],
            np.array([r[2:] for r in block], dtype=np.float64),
        )
        i = j
    all_days = {day for dates, _ in arrays.values() for day in dates}
    calendar = arrays[spy][0] if spy in arrays else sorted(all_days)
    market = Market.from_arrays(arrays, calendar)
    _MARKET_CACHE.clear()
    _MARKET_CACHE[key] = (market, spy)
    return market, spy


def _benchmark_values(
    market: Market, spy: int | None, days: list[date], capital: float
) -> list[float]:
    values, level, previous = [], capital, None
    for day in days:
        bar = market.bar(spy, day) if spy else None
        if bar is not None and previous is not None and previous > 0:
            level *= (bar.close + bar.dividend) * bar.split / previous
        if bar is not None:
            previous = bar.close
        values.append(level)
    return values


def _risk_free(db: Session, days: list[date]) -> tuple[list[float], bool]:
    history = MacroHistory(db, ["DTB3"])
    if not history.has("DTB3"):
        return [0.0] * len(days), False
    out = []
    for point in history.latest_on_each(days, "DTB3"):
        rate = point.value / 100 if point else 0.0
        out.append((1 + rate) ** (1 / 252) - 1)
    return out, True


def _thin(days: list[date], values: list[float], step: int = 5) -> list[tuple[str, float]]:
    points = [(d.isoformat(), v) for d, v in zip(days, values, strict=True)][::step]
    if days and points[-1][0] != days[-1].isoformat():
        points.append((days[-1].isoformat(), values[-1]))
    return points


def _drawdowns(values: list[float]) -> list[float]:
    peak, out = 0.0, []
    for v in values:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def run_backtest(db: Session, spec: BacktestIn, owner_id: Any) -> Backtest:
    started = time.perf_counter()
    dates = signal_dates(db, spec.start, spec.end, spec.rebalance)
    record = Backtest(
        owner_id=owner_id,
        name=spec.name,
        spec=spec.model_dump(mode="json"),
        status="failed",
        summary={},
        results={},
        code_version=CODE_VERSION,
        duration_ms=0,
    )
    if len(dates) < 13:
        record.error = "Fewer than 13 signal dates in the chosen period; widen the date range."
        return _save(db, record, started)
    strategy, equal, coverage = build_rebalances(db, spec, dates)
    if not strategy:
        record.error = "No universe member had scores for the chosen factors in this period."
        return _save(db, record, started)
    end = spec.end or date.today()
    securities = {s for r in strategy + equal for s in r.weights}
    market, spy = load_market(db, securities, dates[0], end)
    costs = CostModel(**spec.costs.model_dump())
    first_trade = next((d for d in market.calendar if d > dates[0]), None)
    if first_trade is None:
        record.error = "No prices after the first signal date."
        return _save(db, record, started)
    sim = simulate(market, strategy, capital=spec.capital, costs=costs, start=first_trade, end=end)
    ew = simulate(market, equal, capital=spec.capital, costs=costs, start=first_trade, end=end)
    days = [s.day for s in sim.days]
    values = [s.nav for s in sim.days]
    ew_values = [s.nav for s in ew.days]
    # The same strategy with no costs at all, to show what the cost assumptions take away.
    gross = simulate(
        market, strategy, capital=spec.capital, costs=ZERO_COSTS, start=first_trade, end=end
    )
    gross_values = [s.nav for s in gross.days]
    spy_values = _benchmark_values(market, spy, days, spec.capital)
    rf, rf_available = _risk_free(db, days)
    trials, variance = _trials(db, owner_id, days[0], days[-1])
    stats = perf.summary(
        days, values, spy_values, rf, trials=trials, trial_sharpe_variance=variance
    )
    ew_stats = perf.summary(days, ew_values, spy_values, rf)
    spy_stats = perf.summary(days, spy_values, spy_values, rf)
    periods = {
        name: perf.period_returns(days, v)
        for name, v in (("strategy", values), ("equal_weight", ew_values), ("spy", spy_values))
    }
    names = _names(db, securities)
    record.data_through = days[-1]
    record.status = "done"
    record.summary = _headline(stats, spy_stats, ew_stats)
    record.results = {
        "stats": {
            "strategy": _jsonable(stats),
            "equal_weight": _jsonable(ew_stats),
            "before_costs": _jsonable(perf.summary(days, gross_values, spy_values, rf)),
            "spy": _jsonable(spy_stats),
        },
        "series": {
            "strategy": _thin(days, values),
            "before_costs": _thin(days, gross_values),
            "equal_weight": _thin(days, ew_values),
            "spy": _thin(days, spy_values),
            "drawdown": _thin(days, _drawdowns(values)),
            "spy_drawdown": _thin(days, _drawdowns(spy_values)),
            "rolling_excess_12m": [
                (d.isoformat(), v) for d, v in perf.rolling_excess(days, values, spy_values)
            ],
        },
        "periods": periods,
        "hit_rate_vs_spy": perf.monthly_hit_rate(
            periods["strategy"]["months"], periods["spy"]["months"]
        ),
        "trading": _trading(sim, values, days, spec.capital),
        "equal_weight_trading": _trading(ew, ew_values, days, spec.capital),
        "holdings": _latest_holdings(sim, names, strategy),
        "coverage": coverage,
        "risk_free": "3-month Treasury bill (FRED DTB3), as published"
        if rf_available
        else "not loaded; Sharpe uses 0%",
        "warnings": sim.warnings
        + [
            f"Exited {names.get(s, ('?', ''))[0]} on {d.isoformat()} at its last price "
            "(no trading for 10+ days)."
            for d, s in sim.stale_exits[:20]
        ],
        "assumptions": [
            "Signals use factor scores as of each month end; orders execute at the next "
            "trading day's open.",
            "Fractional shares; idle cash earns nothing; dividends are paid in cash on the "
            "ex-date.",
            "Long only. Costs: commission, half the estimated bid-ask spread, square-root "
            "market impact; orders above the participation limit are cut.",
            "Universe members without price history cannot be held; see coverage for how many.",
        ],
    }
    return _save(db, record, started)


def _trials(db: Session, owner_id: Any, start: date, end: date) -> tuple[int, float]:
    """This owner's earlier completed backtests over an overlapping period (for the DSR)."""
    rows: list[dict[str, Any]] = list(
        db.scalars(
            select(Backtest.summary).where(Backtest.owner_id == owner_id, Backtest.status == "done")
        ).all()
    )
    sharpes = [
        r["active_sharpe_daily"]
        for r in rows
        if r.get("active_sharpe_daily") is not None
        and r.get("start", "") <= end.isoformat()
        and r.get("end", "") >= start.isoformat()
    ]
    trials = len(sharpes) + 1
    if len(sharpes) < 2:
        return trials, 0.0
    mean = sum(sharpes) / len(sharpes)
    return trials, sum((s - mean) ** 2 for s in sharpes) / (len(sharpes) - 1)


def _headline(stats: dict[str, Any], spy: dict[str, Any], ew: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": stats["start"].isoformat(),
        "end": stats["end"].isoformat(),
        "cagr": stats["cagr"],
        "volatility": stats["volatility"],
        "sharpe": stats["sharpe"],
        "active_sharpe_daily": stats["active_sharpe_daily"],
        "max_drawdown": stats["max_drawdown"],
        "information_ratio": stats["information_ratio"],
        "spy_cagr": spy["cagr"],
        "equal_weight_cagr": ew["cagr"],
        "dsr": stats["dsr"],
        "psr": stats["psr"],
        "trials": stats["trials"],
    }


def _trading(
    sim: SimulationResult, values: list[float], days: list[date], capital: float
) -> dict[str, Any]:
    costs = sim.costs
    years = max((days[-1] - days[0]).days / 365.25, 1e-9)
    average_nav = sum(values) / len(values)
    turnovers = [t for _, t in sim.turnover]
    return {
        "trades": len(sim.trades),
        "capped_orders": sum(t.capped for t in sim.trades),
        "rebalances": len(sim.turnover),
        "average_turnover": sum(turnovers[1:]) / len(turnovers[1:]) if len(turnovers) > 1 else None,
        "annual_turnover": sum(turnovers[1:]) / years if len(turnovers) > 1 else None,
        "costs": costs,
        "costs_total": sum(costs.values()),
        "costs_bps_per_year": sum(costs.values()) / average_nav / years * 10_000
        if average_nav
        else None,
        "costs_share_of_capital": sum(costs.values()) / capital,
        "average_positions": sum(s.positions for s in sim.days) / len(sim.days) if sim.days else 0,
    }


def _names(db: Session, securities: set[int]) -> dict[int, tuple[str, str]]:
    rows = db.execute(
        select(Security.id, Security.ticker, Company.legal_name)
        .join(Company, Company.id == Security.company_id)
        .where(Security.id.in_(securities))
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _latest_holdings(
    sim: SimulationResult, names: dict[int, tuple[str, str]], rebalances: list[Rebalance]
) -> dict[str, Any]:
    if not sim.holdings:
        return {"as_of": None, "positions": []}
    day, weights = sim.holdings[-1]
    signal = rebalances[-1].signal_date if rebalances else None
    positions = [
        {"ticker": names.get(s, ("?", ""))[0], "name": names.get(s, ("", "?"))[1], "weight": w}
        for s, w in sorted(weights.items(), key=lambda kv: -kv[1])
    ]
    return {
        "as_of": day.isoformat(),
        "signal_date": signal.isoformat() if signal else None,
        "positions": positions,
    }


def _jsonable(stats: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in stats.items()}


def _save(db: Session, record: Backtest, started: float) -> Backtest:
    record.duration_ms = int((time.perf_counter() - started) * 1000)
    db.add(record)
    db.commit()
    return record


def options(db: Session) -> dict[str, Any]:
    window = select(func.min(FactorScore.as_of), func.max(FactorScore.as_of)).where(
        FactorScore.version == FACTOR_VERSION
    )
    first, last = db.execute(window).one()
    defaults = CostModel()
    return {
        "factors": [
            {"key": k, "label": LABELS[k], "inputs": [name for name, _ in FACTORS[k]]}
            for k in FACTORS
        ],
        "first_signal": first,
        "last_signal": last,
        "universe_size": int(
            db.scalar(
                select(func.count()).where(
                    UniverseMember.version == UNIVERSE_VERSION,
                    UniverseMember.as_of
                    == select(func.max(UniverseMember.as_of)).scalar_subquery(),
                )
            )
            or 0
        ),
        "costs": [
            {"key": k, "label": COST_LABELS[k][0], "value": v, "explanation": COST_LABELS[k][1]}
            for k, v in asdict(defaults).items()
            if k in COST_LABELS
        ],
        "notes": [
            "Backtests use the Phase 6 point-in-time universe: the 200 largest companies by SEC "
            "revenue known at each month end. Companies later delisted have no prices and cannot "
            "be held, which biases results toward survivors (coverage is reported per date).",
            "Every backtest you run counts toward the deflated Sharpe ratio, which discounts the "
            "best of many tries.",
        ],
    }
