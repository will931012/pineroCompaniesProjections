"""Backtest engine and performance statistics against hand-computed cases."""

import math
from datetime import date, timedelta

import pytest

from app.analytics import performance as perf
from app.analytics.backtest import (
    ZERO_COSTS,
    Bar,
    CostModel,
    Market,
    Rebalance,
    corwin_schultz,
    order_costs,
    simulate,
)
from app.backtesting.service import _cap_weights


def _days(start: date, n: int) -> list[date]:
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _flat(days: list[date], price: float = 100.0, volume: float = 1e6) -> dict[date, Bar]:
    return {d: Bar(price, price * 1.01, price * 0.99, price, volume) for d in days}


def test_corwin_schultz_matches_its_closed_form() -> None:
    # Identical days with range x = ln(H/L) give α = x, so S = 2(eˣ − 1)/(1 + eˣ).
    x = math.log(101 / 99)
    expected = 2 * (math.exp(x) - 1) / (1 + math.exp(x))
    assert corwin_schultz([101, 101, 101], [99, 99, 99]) == pytest.approx(expected)
    assert corwin_schultz([100], [99]) is None


def test_order_costs_follow_the_stated_formulas() -> None:
    model = CostModel(
        commission_per_share=0.01,
        min_commission=1.0,
        spread_fallback=0.001,
        spread_cap=0.02,
        impact_coefficient=0.5,
    )
    commission, spread, impact = order_costs(
        model, 1000, 50.0, adv=5_000_000, vol=0.02, spread=0.004
    )
    value = 50_000
    assert commission == pytest.approx(10.0)
    assert spread == pytest.approx(0.002 * value)
    assert impact == pytest.approx(0.5 * 0.02 * math.sqrt(value / 5_000_000) * value)
    small = order_costs(model, 10, 50.0, adv=None, vol=None, spread=None)
    assert small == pytest.approx((1.0, 0.0005 * 500, 0.0))  # minimum commission, fallback spread


def test_buy_and_hold_through_a_split_and_a_dividend() -> None:
    days = _days(date(2024, 1, 1), 60)
    bars = _flat(days)
    # 2-for-1 split on day 30 (price halves), then a $1 dividend on day 40.
    for d in days[30:]:
        bars[d] = Bar(50, 50.5, 49.5, 50, 2e6, 0.0, 2.0 if d == days[30] else 1.0)
    bars[days[40]] = Bar(50, 50.5, 49.5, 50, 2e6, 1.0, 1.0)
    market = Market({1: bars}, days)
    result = simulate(
        market,
        [Rebalance(days[0], {1: 1.0})],
        capital=10_000,
        costs=ZERO_COSTS,
        start=days[0],
        end=days[-1],
    )
    trade = result.trades[0]
    assert trade.day == days[1] and trade.price == 100  # next day's open
    assert result.days[-1].nav == pytest.approx(10_000 + 200 * 1.0)  # 200 shares after the split
    assert result.days[35].nav == pytest.approx(10_000)  # the split itself changes nothing


def test_orders_execute_at_the_next_open_without_look_ahead() -> None:
    days = _days(date(2024, 3, 1), 30)
    bars = _flat(days)
    signal = days[10]
    # A huge close on the signal day must not be the execution price.
    bars[signal] = Bar(100, 200, 99, 200, 1e6)
    bars[days[11]] = Bar(105, 106, 104, 105, 1e6)
    result = simulate(
        Market({1: bars}, days),
        [Rebalance(signal, {1: 0.5})],
        capital=1_000,
        costs=ZERO_COSTS,
        start=days[0],
        end=days[-1],
    )
    assert result.trades[0].day == days[11] and result.trades[0].price == 105


def test_participation_cap_cuts_large_orders() -> None:
    days = _days(date(2024, 1, 1), 40)
    bars = _flat(days, volume=100)  # $10,000 a day
    model = CostModel(max_participation=0.1)
    result = simulate(
        Market({1: bars}, days),
        [Rebalance(days[25], {1: 1.0})],
        capital=1_000_000,
        costs=model,
        start=days[0],
        end=days[-1],
    )
    trade = result.trades[0]
    assert trade.capped and trade.value == pytest.approx(1_000)  # 10% of $10,000
    assert all(s.cash >= -1e-6 for s in result.days)


def test_rebalance_sells_first_and_never_overdraws_cash() -> None:
    days = _days(date(2024, 1, 1), 80)
    market = Market({1: _flat(days), 2: _flat(days, 50)}, days)
    model = CostModel(commission_per_share=0.01, impact_coefficient=1.0)
    result = simulate(
        market,
        [Rebalance(days[25], {1: 1.0}), Rebalance(days[50], {2: 1.0})],
        capital=100_000,
        costs=model,
        start=days[0],
        end=days[-1],
    )
    second = [t for t in result.trades if t.day == days[51]]
    assert second[0].shares < 0 and second[0].security == 1  # the sell goes first
    assert all(s.cash >= -1e-6 for s in result.days)
    assert result.days[-1].positions == 1
    assert not result.warnings


def test_positions_that_stop_trading_are_exited_at_their_last_price() -> None:
    days = _days(date(2024, 1, 1), 60)
    stopped = {d: b for d, b in _flat(days, 40).items() if d <= days[20]}
    market = Market({1: stopped, 2: _flat(days)}, days)
    result = simulate(
        market,
        [Rebalance(days[5], {1: 0.5, 2: 0.5}), Rebalance(days[45], {1: 0.5, 2: 0.5})],
        capital=10_000,
        costs=ZERO_COSTS,
        start=days[0],
        end=days[-1],
    )
    assert result.stale_exits == [(days[46], 1)]
    exit_trade = next(t for t in result.trades if t.security == 1 and t.shares < 0)
    assert exit_trade.price == 40


def test_weight_cap_redistributes_the_excess() -> None:
    weights = _cap_weights({1: 6.0, 2: 2.0, 3: 2.0}, cap=0.4)
    assert weights[1] == pytest.approx(0.4)
    assert weights[2] == pytest.approx(0.3) and weights[3] == pytest.approx(0.3)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_performance_statistics() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(366)]
    doubling = [100 * 2 ** (i / 365) for i in range(366)]
    flat_rf = [0.0] * len(days)
    stats = perf.summary(days, doubling, doubling, flat_rf)
    assert stats["cagr"] == pytest.approx(2 ** (365.25 / 365) - 1, rel=1e-3)
    assert stats["max_drawdown"] == 0
    bumpy = [100.0, 120.0, 90.0, 95.0, 130.0]
    dd = perf.drawdown_stats(bumpy, days[:5])
    assert dd["max_drawdown"] == pytest.approx(-0.25) and dd["max_drawdown_trough"] == days[2]
    months = perf.period_returns(days, doubling)["months"]
    assert len(months) == 12  # 2020-01-01 + 365 days ends on 2020-12-31 (leap year)


def test_probabilistic_and_deflated_sharpe() -> None:
    psr = perf.probabilistic_sharpe(0.05, 1000, 0.0, 3.0)
    assert psr is not None and psr > 0.9
    assert perf.probabilistic_sharpe(0.0, 1000, 0.0, 3.0) == pytest.approx(0.5)
    threshold = perf.expected_max_sharpe(20, 0.0004)
    assert threshold > perf.expected_max_sharpe(5, 0.0004) > 0
    deflated = perf.probabilistic_sharpe(0.05, 1000, 0.0, 3.0, threshold)
    assert deflated is not None and deflated < psr
