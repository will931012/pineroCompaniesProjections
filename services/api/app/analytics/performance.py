"""Performance statistics for a daily value series against a benchmark. Pure functions.

Returns are daily simple returns of the value series; the risk-free rate is a daily series
(from the 3-month T-bill) aligned to the same days. Annualisation uses 252 trading days.

Probabilistic Sharpe ratio (Bailey & López de Prado, 2012): the probability that the true
Sharpe ratio exceeds a threshold given the sample's length, skew, and kurtosis. The deflated
Sharpe ratio (2014) raises the threshold to the Sharpe ratio expected from the best of N
independent tries with no skill, so testing many variants is not mistaken for skill.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from itertools import pairwise
from statistics import NormalDist
from typing import Any

PERIODS = 252
EULER = 0.5772156649015329
_N = NormalDist()


def returns_of(values: Sequence[float]) -> list[float]:
    return [b / a - 1 if a > 0 else 0.0 for a, b in pairwise(values)]


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def moments(xs: Sequence[float]) -> tuple[float, float]:
    """(skewness, kurtosis) — kurtosis is not excess (normal = 3)."""
    n, m, s = len(xs), _mean(xs), _std(xs)
    if n < 3 or s == 0:
        return 0.0, 3.0
    skew = sum(((x - m) / s) ** 3 for x in xs) / n
    kurt = sum(((x - m) / s) ** 4 for x in xs) / n
    return skew, kurt


def drawdown_stats(values: Sequence[float], days: Sequence[date]) -> dict[str, Any]:
    peak, peak_day = -math.inf, days[0]
    worst, worst_peak, worst_trough = 0.0, days[0], days[0]
    longest, current_start = 0, days[0]
    for value, day in zip(values, days, strict=True):
        if value >= peak:
            peak, peak_day = value, day
            current_start = day
        drawdown = value / peak - 1 if peak > 0 else 0.0
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_day, day
        longest = max(longest, (day - current_start).days)
    return {
        "max_drawdown": worst,
        "max_drawdown_peak": worst_peak,
        "max_drawdown_trough": worst_trough,
        "longest_drawdown_days": longest,
    }


def probabilistic_sharpe(
    sharpe: float, n: int, skew: float, kurtosis: float, threshold: float = 0.0
) -> float | None:
    """P(true per-period Sharpe > threshold); inputs are per-period, not annualised."""
    if n < 3:
        return None
    denominator = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if denominator <= 0:
        return None
    return _N.cdf((sharpe - threshold) * math.sqrt(n - 1) / math.sqrt(denominator))


def expected_max_sharpe(trials: int, variance: float) -> float:
    """Sharpe ratio expected from the best of `trials` unskilled strategies (per period)."""
    if trials < 2 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER) * _N.inv_cdf(1 - 1 / trials) + EULER * _N.inv_cdf(1 - 1 / (trials * math.e))
    )


def summary(
    days: Sequence[date],
    values: Sequence[float],
    benchmark: Sequence[float],
    risk_free: Sequence[float],
    *,
    trials: int = 1,
    trial_sharpe_variance: float = 0.0,
) -> dict[str, Any]:
    """Statistics for `values` (and the same for `benchmark`) over `days`; rf is daily."""
    r = returns_of(values)
    rb = returns_of(benchmark)
    rf = list(risk_free[1:])
    excess = [a - f for a, f in zip(r, rf, strict=True)]
    excess_b = [a - f for a, f in zip(rb, rf, strict=True)]
    active = [a - b for a, b in zip(r, rb, strict=True)]
    years = (days[-1] - days[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[0] > 0 else None
    vol = _std(r) * math.sqrt(PERIODS)
    sharpe_daily = _mean(excess) / _std(excess) if _std(excess) > 0 else 0.0
    downside = [min(0.0, x) for x in excess]
    downside_dev = math.sqrt(sum(x * x for x in downside) / len(downside)) if downside else 0.0
    var_b = _std(excess_b) ** 2
    cov = (
        sum(
            (a - _mean(excess)) * (b - _mean(excess_b))
            for a, b in zip(excess, excess_b, strict=True)
        )
        / (len(excess) - 1)
        if len(excess) > 1
        else 0.0
    )
    beta = cov / var_b if var_b > 0 else None
    alpha = (_mean(excess) - (beta or 0) * _mean(excess_b)) * PERIODS if beta is not None else None
    tracking = _std(active) * math.sqrt(PERIODS)
    skew, kurt = moments(excess)
    dd = drawdown_stats(values, days)
    # PSR and DSR test the active return (strategy minus benchmark): "did it beat the index?",
    # not "was it positive?", which a long-only equity strategy almost always is.
    active_std = _std(active)
    active_sharpe = _mean(active) / active_std if active_std > 0 else None
    active_skew, active_kurt = moments(active)
    threshold = expected_max_sharpe(trials, trial_sharpe_variance)
    return {
        "start": days[0],
        "end": days[-1],
        "years": years,
        "total_return": values[-1] / values[0] - 1,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe_daily * math.sqrt(PERIODS),
        "sortino": _mean(excess) / downside_dev * math.sqrt(PERIODS) if downside_dev > 0 else None,
        "calmar": cagr / abs(dd["max_drawdown"])
        if cagr is not None and dd["max_drawdown"] < 0
        else None,
        "beta": beta,
        "alpha": alpha,
        "tracking_error": tracking,
        "information_ratio": _mean(active) * PERIODS / tracking if tracking > 0 else None,
        "skew": skew,
        "kurtosis": kurt,
        "active_sharpe_daily": active_sharpe,
        "psr": probabilistic_sharpe(active_sharpe, len(active), active_skew, active_kurt)
        if active_sharpe is not None
        else None,
        "dsr": probabilistic_sharpe(active_sharpe, len(active), active_skew, active_kurt, threshold)
        if active_sharpe is not None and trials > 1
        else None,
        "trials": trials,
        **dd,
    }


def period_returns(days: Sequence[date], values: Sequence[float]) -> dict[str, dict[str, float]]:
    """Calendar-month and calendar-year returns from end-of-period values."""
    month_end: dict[tuple[int, int], float] = {}
    year_end: dict[int, float] = {}
    for day, value in zip(days, values, strict=True):
        month_end[(day.year, day.month)] = value
        year_end[day.year] = value
    months, years = {}, {}
    previous = values[0]
    for (y, m), value in sorted(month_end.items()):
        months[f"{y}-{m:02d}"] = value / previous - 1
        previous = value
    previous = values[0]
    for y, value in sorted(year_end.items()):
        years[str(y)] = value / previous - 1
        previous = value
    return {"months": months, "years": years}


def monthly_hit_rate(months: dict[str, float], benchmark_months: dict[str, float]) -> float | None:
    shared = [k for k in months if k in benchmark_months]
    if not shared:
        return None
    return sum(months[k] > benchmark_months[k] for k in shared) / len(shared)


def rolling_excess(
    days: Sequence[date], values: Sequence[float], benchmark: Sequence[float], window: int = 252
) -> list[tuple[date, float]]:
    out = []
    for i in range(window, len(values), 5):
        own = values[i] / values[i - window] - 1
        bench = benchmark[i] / benchmark[i - window] - 1
        out.append((days[i], own - bench))
    return out


def group_by_year(trades: Sequence[tuple[date, float]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for day, value in trades:
        out[str(day.year)] += value
    return dict(out)
