"""Daily long-only portfolio simulation with estimated trading costs. Pure functions; no I/O.

Each trading day, in order:
1. Splits: share counts are multiplied by the day's split factor (raw prices drop the same day).
2. Dividends: cash receives dividend × shares held at the start of the day (ex-date).
3. Rebalance (the first trading day after a signal date): orders execute at the day's open.
   Sells go first; buys are scaled down if cash after costs would go negative.
4. Mark to market at the close; a security without a bar keeps its last close.

Costs for each order, using only data from before the execution day:
- commission: max(minimum, per-share rate × shares);
- spread: half the bid-ask spread, estimated from daily highs and lows (Corwin–Schultz, 2012)
  over the previous month, bounded to [0, cap], with a stated fallback when not estimable;
- market impact: coefficient × daily volatility × √(order value ÷ average daily dollar volume)
  × order value (the square-root law);
- liquidity: an order is cut to at most `max_participation` × 20-day average dollar volume.

Shares are fractional (no rounding), and idle cash earns nothing.
"""

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np

CODE_VERSION = "b1"


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    dividend: float = 0.0
    split: float = 1.0


@dataclass(frozen=True)
class CostModel:
    commission_per_share: float = 0.005
    min_commission: float = 1.0
    spread_fallback: float = 0.0010  # full spread when highs/lows give no estimate
    spread_cap: float = 0.02
    impact_coefficient: float = 0.5
    max_participation: float = 0.10
    lookback_days: int = 21


ZERO_COSTS = CostModel(0.0, 0.0, 0.0, 0.0, 0.0, 1e9, 21)


@dataclass(frozen=True)
class Rebalance:
    signal_date: date
    weights: dict[int, float]  # security -> target weight (sum ≤ 1); the rest stays in cash


@dataclass(frozen=True)
class Trade:
    day: date
    security: int
    shares: float  # positive buy, negative sell
    price: float
    value: float  # |shares| × price
    commission: float
    spread_cost: float
    impact_cost: float
    capped: bool  # cut by the participation limit


@dataclass
class Snapshot:
    day: date
    nav: float
    cash: float
    positions: int


@dataclass
class SimulationResult:
    days: list[Snapshot] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    turnover: list[tuple[date, float]] = field(default_factory=list)  # one-way, fraction of NAV
    holdings: list[tuple[date, dict[int, float]]] = field(default_factory=list)  # weights after
    stale_exits: list[tuple[date, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def costs(self) -> dict[str, float]:
        return {
            "commission": sum(t.commission for t in self.trades),
            "spread": sum(t.spread_cost for t in self.trades),
            "impact": sum(t.impact_cost for t in self.trades),
        }


def corwin_schultz(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float] | None = None
) -> float | None:
    """Average bid-ask spread (fraction of price) from consecutive daily highs and lows.

    Each two-day window: β = ln(H₁/L₁)² + ln(H₂/L₂)², γ = ln(max H / min L)²,
    α = (√(2β) − √β)/(3 − 2√2) − √(γ/(3 − 2√2)), S = 2(eᵅ − 1)/(1 + eᵅ); negative S → 0.
    With `closes`, day two's range is shifted by any overnight gap first (the paper's
    adjustment). The estimator overstates spreads for very liquid stocks: it reads ~20–40 bps
    for the largest US stocks, whose quoted spreads are a few basis points.
    """
    k = 3 - 2 * math.sqrt(2)
    estimates = []
    for i in range(len(highs) - 1):
        h1, l1, h2, l2 = highs[i], lows[i], highs[i + 1], lows[i + 1]
        if min(h1, l1, h2, l2) <= 0 or h1 < l1 or h2 < l2:
            continue
        if closes is not None:
            close = closes[i]
            if l2 > close:  # gap up overnight
                h2, l2 = h2 - (l2 - close), close
            elif h2 < close:  # gap down overnight
                h2, l2 = close, l2 + (close - h2)
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
        spread = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        estimates.append(max(spread, 0.0))
    return sum(estimates) / len(estimates) if estimates else None


def _pair_spread(h1: float, l1: float, h2: float, l2: float, close: float) -> float:
    """One Corwin–Schultz two-day estimate with the overnight-gap adjustment; NaN if invalid."""
    if min(h1, l1, h2, l2) <= 0 or h1 < l1 or h2 < l2:
        return math.nan
    if l2 > close:
        h2, l2 = h2 - (l2 - close), close
    elif h2 < close:
        h2, l2 = close, l2 + (close - h2)
    k = 3 - 2 * math.sqrt(2)
    beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
    gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
    return max(2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha)), 0.0)


class _Series:
    """One security's bars as arrays, with running sums for O(1) trailing statistics."""

    def __init__(self, dates: list[date], rows: np.ndarray) -> None:
        self.dates = dates
        self.index = {d: i for i, d in enumerate(dates)}
        self.rows = rows  # columns: open, high, low, close, volume, dividend, split
        _open, h, low, c, v, div, split = (rows[:, j] for j in range(7))
        n = len(dates)
        self.cum_dollar = np.concatenate([[0.0], np.cumsum(c * v)])
        ret = np.zeros(n)
        if n > 1:
            previous = c[:-1]
            with np.errstate(divide="ignore", invalid="ignore"):
                ret[1:] = np.where(previous > 0, (c[1:] + div[1:]) * split[1:] / previous - 1, 0.0)
        self.cum_ret = np.concatenate([[0.0], np.cumsum(ret)])
        self.cum_ret2 = np.concatenate([[0.0], np.cumsum(ret**2)])
        pairs = np.full(n, np.nan)
        for i in range(1, n):
            if split[i] == 1.0:
                pairs[i] = _pair_spread(h[i - 1], low[i - 1], h[i], low[i], c[i - 1])
        valid = ~np.isnan(pairs)
        self.cum_spread = np.concatenate([[0.0], np.cumsum(np.where(valid, pairs, 0.0))])
        self.cum_spread_n = np.concatenate([[0], np.cumsum(valid)])

    def bar(self, i: int) -> Bar:
        r = self.rows[i]
        return Bar(float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                   float(r[5]), float(r[6]))  # fmt: skip


class Market:
    """Bars by security and date, with the trailing statistics the cost model needs."""

    def __init__(self, bars: dict[int, dict[date, Bar]], calendar: Sequence[date]) -> None:
        series = {}
        for security, by_day in bars.items():
            dates = sorted(by_day)
            rows = np.array(
                [[b.open, b.high, b.low, b.close, b.volume, b.dividend, b.split]
                 for b in (by_day[d] for d in dates)],
                dtype=np.float64,
            ).reshape(len(dates), 7)  # fmt: skip
            series[security] = _Series(dates, rows)
        self._init(series, calendar)

    @classmethod
    def from_arrays(
        cls, arrays: dict[int, tuple[list[date], np.ndarray]], calendar: Sequence[date]
    ) -> "Market":
        market = cls.__new__(cls)
        market._init({s: _Series(d, r) for s, (d, r) in arrays.items()}, calendar)
        return market

    def _init(self, series: dict[int, "_Series"], calendar: Sequence[date]) -> None:
        self._series = series
        self.calendar = list(calendar)
        self._calendar_index = {d: i for i, d in enumerate(self.calendar)}

    @property
    def securities(self) -> set[int]:
        return set(self._series)

    def bar(self, security: int, day: date) -> Bar | None:
        series = self._series.get(security)
        if series is None:
            return None
        i = series.index.get(day)
        return series.bar(i) if i is not None else None

    def calendar_position(self, day: date) -> int | None:
        return self._calendar_index.get(day)

    def last_close(self, security: int, day: date) -> tuple[float, date] | None:
        series = self._series.get(security)
        if series is None:
            return None
        i = bisect.bisect_right(series.dates, day) - 1
        if i < 0:
            return None
        return float(series.rows[i, 3]), series.dates[i]

    def trailing(
        self, security: int, day: date, n: int
    ) -> tuple[float | None, float | None, float | None]:
        """(average dollar volume, daily volatility, spread) over the `n` bars before `day`."""
        series = self._series.get(security)
        if series is None:
            return None, None, None
        i = bisect.bisect_left(series.dates, day)
        if i < 3:
            return None, None, None
        lo = max(0, i - n)
        adv = float(series.cum_dollar[i] - series.cum_dollar[lo]) / (i - lo)
        # Returns of bars lo..i-1 (bar 0 has none).
        r_lo = max(1, lo)
        count = i - r_lo
        vol = None
        if count >= 2:
            total = series.cum_ret[i] - series.cum_ret[r_lo]
            squares = series.cum_ret2[i] - series.cum_ret2[r_lo]
            variance = (squares - total * total / count) / (count - 1)
            vol = math.sqrt(max(float(variance), 0.0))
        pairs = int(series.cum_spread_n[i] - series.cum_spread_n[r_lo])
        spread = (
            float(series.cum_spread[i] - series.cum_spread[r_lo]) / pairs if pairs > 0 else None
        )
        return (adv if adv > 0 else None), vol, spread


def order_costs(
    model: CostModel, shares: float, price: float, adv: float | None, vol: float | None,
    spread: float | None,
) -> tuple[float, float, float]:  # fmt: skip
    """(commission, spread cost, impact cost) for one order."""
    value = abs(shares) * price
    if value <= 0:
        return 0.0, 0.0, 0.0
    commission = max(model.min_commission, model.commission_per_share * abs(shares))
    full_spread = spread if spread is not None and spread > 0 else model.spread_fallback
    spread_cost = min(full_spread, model.spread_cap) / 2 * value
    impact = 0.0
    if adv and vol:
        impact = model.impact_coefficient * vol * math.sqrt(value / adv) * value
    return commission, spread_cost, impact


STALE_DAYS = 10


def simulate(
    market: Market,
    rebalances: Sequence[Rebalance],
    *,
    capital: float,
    costs: CostModel,
    start: date,
    end: date,
) -> SimulationResult:
    result = SimulationResult()
    days = [d for d in market.calendar if start <= d <= end]
    pending = sorted(rebalances, key=lambda r: r.signal_date)
    shares: dict[int, float] = {}
    cash = capital
    next_rebalance = 0
    for day in days:
        # 1–2. Corporate actions on positions held from the previous close.
        for security in list(shares):
            bar = market.bar(security, day)
            if bar is None:
                continue
            if bar.split != 1.0:
                shares[security] *= bar.split
            if bar.dividend:
                cash += bar.dividend * shares[security]

        # 3. Rebalance at today's open when a signal date has passed.
        due = None
        while next_rebalance < len(pending) and pending[next_rebalance].signal_date < day:
            due = pending[next_rebalance]
            next_rebalance += 1
        if due is not None:
            cash = _rebalance(market, day, due, shares, cash, costs, result)

        # 4. Mark to market at the close.
        value = 0.0
        for security, quantity in shares.items():
            last = market.last_close(security, day)
            value += quantity * (last[0] if last else 0.0)
        result.days.append(Snapshot(day, cash + value, cash, len(shares)))
    return result


def _price_for_trade(market: Market, security: int, day: date) -> tuple[float, bool] | None:
    """Today's open, or (for an exit only) the last close when the security stopped trading."""
    bar = market.bar(security, day)
    if bar is not None and bar.open > 0:
        return bar.open, False
    last = market.last_close(security, day)
    if last is None:
        return None
    position = market.calendar_position(day)
    stale_since = bisect.bisect_left(market.calendar, last[1])
    if position is not None and position - stale_since >= STALE_DAYS:
        return last[0], True
    return None


def _rebalance(
    market: Market,
    day: date,
    rebalance: Rebalance,
    shares: dict[int, float],
    cash: float,
    model: CostModel,
    result: SimulationResult,
) -> float:
    prices: dict[int, float] = {}
    stale: set[int] = set()
    for security in set(shares) | set(rebalance.weights):
        found = _price_for_trade(market, security, day)
        if found is not None:
            prices[security], is_stale = found
            if is_stale:
                stale.add(security)

    def mark(security: int) -> float:
        if security in prices:
            return prices[security]
        last = market.last_close(security, day)
        return last[0] if last else 0.0

    nav = cash + sum(q * mark(s) for s, q in shares.items())
    targets = {
        s: w * nav / prices[s]
        for s, w in rebalance.weights.items()
        if s in prices and s not in stale
    }
    for s in stale:
        targets[s] = 0.0
        result.stale_exits.append((day, s))
    orders: dict[int, float] = {}
    for security in set(shares) | set(targets):
        if security not in prices:
            continue  # no price today: hold until it trades again
        delta = targets.get(security, 0.0) - shares.get(security, 0.0)
        if abs(delta) * prices[security] > 1e-6:
            orders[security] = delta
    traded = 0.0

    def execute(security: int, quantity: float) -> None:
        nonlocal cash, traded
        price = prices[security]
        adv, vol, spread = market.trailing(security, day, model.lookback_days)
        capped = False
        if security in stale:
            adv, vol, spread = None, None, spread  # forced exit: no impact estimate
        elif adv is not None and abs(quantity) * price > model.max_participation * adv:
            quantity = math.copysign(model.max_participation * adv / price, quantity)
            capped = True
        commission, spread_cost, impact = order_costs(model, quantity, price, adv, vol, spread)
        cash -= quantity * price + commission + spread_cost + impact
        shares[security] = shares.get(security, 0.0) + quantity
        if abs(shares[security]) < 1e-9:
            del shares[security]
        traded += abs(quantity) * price
        result.trades.append(
            Trade(day, security, quantity, price, abs(quantity) * price, commission, spread_cost,
                  impact, capped)
        )  # fmt: skip

    for security, quantity in sorted(orders.items(), key=lambda kv: kv[1]):
        if quantity < 0:
            execute(security, quantity)
    buys = {s: q for s, q in orders.items() if q > 0}
    # Size buys so that their value plus estimated costs fits the cash after sells.
    needed = 0.0
    for security, quantity in buys.items():
        price = prices[security]
        adv, vol, spread = market.trailing(security, day, model.lookback_days)
        needed += quantity * price + sum(order_costs(model, quantity, price, adv, vol, spread))
    scale = min(1.0, max(cash, 0.0) / needed) if needed > 0 else 1.0
    for security, quantity in sorted(buys.items()):
        execute(security, quantity * scale)
    result.turnover.append((day, traded / nav / 2 if nav > 0 else 0.0))
    invested = {s: q * prices.get(s, 0.0) for s, q in shares.items()}
    total = cash + sum(invested.values())
    result.holdings.append((day, {s: v / total for s, v in invested.items() if total > 0}))
    # Cents below zero come from minimum commissions on scaled-down buys; ignore those.
    if cash < -1.0:
        result.warnings.append(f"Cash went negative on {day.isoformat()} ({cash:.2f}).")
    return cash
