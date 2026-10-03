"""Technical indicators and return statistics. Pure functions over float lists, oldest first.

Returns are total returns rebuilt from raw prices: r_t = (close_t + dividend_t) × split_t
÷ close_{t−1} − 1, which matches Tiingo's adjusted closes to within 1e-7 on real data and,
unlike stored adjusted prices, does not depend on when each bar was fetched.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

TRADING_DAYS = 252


def total_returns(
    closes: Sequence[float],
    dividends: Sequence[float] | None = None,
    splits: Sequence[float] | None = None,
) -> list[float]:
    """Daily total returns; one shorter than `closes`."""
    out = []
    for i in range(1, len(closes)):
        dividend = dividends[i] if dividends else 0.0
        split = splits[i] if splits else 1.0
        previous = closes[i - 1]
        out.append((closes[i] + dividend) * split / previous - 1 if previous > 0 else 0.0)
    return out


def growth_index(returns: Sequence[float], start: float = 1.0) -> list[float]:
    """Cumulative value of `start` invested; one longer than `returns`."""
    values = [start]
    for r in returns:
        values.append(values[-1] * (1 + r))
    return values


def sma(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        out.append(running / window if i >= window - 1 else None)
    return out


def ema(values: Sequence[float], window: int) -> list[float | None]:
    """Exponential average seeded with the simple average of the first `window` values."""
    out: list[float | None] = [None] * len(values)
    if len(values) < window:
        return out
    alpha = 2 / (window + 1)
    current = sum(values[:window]) / window
    out[window - 1] = current
    for i in range(window, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        out[i] = current
    return out


def rsi(values: Sequence[float], window: int = 14) -> list[float | None]:
    """Wilder's relative strength index (0–100)."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= window:
        return out
    gains = losses = 0.0
    for i in range(1, window + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    average_gain, average_loss = gains / window, losses / window

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - 100 / (1 + gain / loss)

    out[window] = value(average_gain, average_loss)
    for i in range(window + 1, len(values)):
        change = values[i] - values[i - 1]
        average_gain = (average_gain * (window - 1) + max(change, 0.0)) / window
        average_loss = (average_loss * (window - 1) + max(-change, 0.0)) / window
        out[i] = value(average_gain, average_loss)
    return out


@dataclass(frozen=True)
class Macd:
    line: list[float | None]
    signal: list[float | None]
    histogram: list[float | None]


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    line = [
        f - s if f is not None and s is not None else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    tail = ema([v for v in line[start:] if v is not None], signal)
    signal_line: list[float | None] = [None] * start
    signal_line.extend(tail)
    histogram = [
        lv - sv if lv is not None and sv is not None else None
        for lv, sv in zip(line, signal_line, strict=True)
    ]
    return Macd(line, signal_line, histogram)


def stdev(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def annualised_volatility(returns: Sequence[float], periods: int = TRADING_DAYS) -> float | None:
    sd = stdev(returns)
    return sd * math.sqrt(periods) if sd is not None else None


def rolling_volatility(
    returns: Sequence[float], window: int, periods: int = TRADING_DAYS
) -> list[float | None]:
    return [
        annualised_volatility(returns[i - window + 1 : i + 1], periods) if i >= window - 1 else None
        for i in range(len(returns))
    ]


def drawdowns(index: Sequence[float]) -> list[float]:
    """Fall from the running peak at each point (0 at a new high, −0.5 for half lost)."""
    peak = -math.inf
    out = []
    for v in index:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def max_drawdown(index: Sequence[float]) -> float:
    return min(drawdowns(index), default=0.0)


def period_return(index: Sequence[float], periods: int) -> float | None:
    if len(index) <= periods or index[-1 - periods] <= 0:
        return None
    return index[-1] / index[-1 - periods] - 1


def correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = len(a)
    if n != len(b) or n < 3:
        return None
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / math.sqrt(var_a * var_b)


def beta(asset: Sequence[float], market: Sequence[float]) -> float | None:
    n = len(asset)
    if n != len(market) or n < 3:
        return None
    mean_a, mean_m = sum(asset) / n, sum(market) / n
    var_m = sum((m - mean_m) ** 2 for m in market)
    if var_m <= 0:
        return None
    return sum((a - mean_a) * (m - mean_m) for a, m in zip(asset, market, strict=True)) / var_m


def rolling(
    a: Sequence[float], b: Sequence[float], window: int, statistic: str
) -> list[float | None]:
    """Rolling correlation or beta of `a` on `b` over `window` aligned observations."""
    function = correlation if statistic == "correlation" else beta
    return [
        function(a[i - window + 1 : i + 1], b[i - window + 1 : i + 1]) if i >= window - 1 else None
        for i in range(len(a))
    ]
