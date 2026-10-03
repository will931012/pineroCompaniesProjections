"""Phase 6 calculations, parsers, and the point-in-time (leakage) guarantees."""

import io
import json
import math
import zipfile
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import httpx
import numpy as np
import pytest

from app.analytics import technicals as t
from app.fundamentals.concepts import ACCEPTED_FORMS
from app.fundamentals.service import TRACKED_UNITS
from app.fundamentals.snapshot import PricePoint
from app.providers.fred import Vintage, parse_observations
from app.providers.market_data.tiingo import parse_crypto_bar
from app.providers.openfigi import OpenFigiClient, parse_mapping
from app.providers.sec_edgar import parse_company_facts, parse_frame
from app.quant import bitcoin, modeling
from app.quant.factors import score_date, zscores
from app.quant.features import CompanyInputs, MarketInputs, compute_features
from app.quant.macro import MacroHistory, availability
from app.quant.ownership import (
    Filing,
    Position,
    drop_price_outliers,
    parse_filings,
    read_holdings,
    select_filings,
)
from app.quant.prices import ReturnSeries, last_complete_day
from app.quant.regime import classify
from app.quant.universe import AnnualRevenue, month_ends, revenue_as_of
from tests.fixtures_companyfacts import companyfacts

# --- Technicals ---------------------------------------------------------------------------


def test_moving_averages_and_rsi_match_hand_values() -> None:
    values = [1.0, 2, 3, 4, 5, 6]
    assert t.sma(values, 3) == [None, None, 2.0, 3.0, 4.0, 5.0]
    # EMA seeded with the first SMA (2.0); alpha = 0.5: 3.0, 4.0, 5.0
    assert t.ema(values, 3) == [None, None, 2.0, 3.0, 4.0, 5.0]
    rising = [float(i) for i in range(20)]
    assert t.rsi(rising, 14)[-1] == 100.0
    flat = [5.0] * 20
    assert t.rsi(flat, 14)[-1] == 50.0
    # Alternating +1/−1 moves: equal average gain and loss → RSI 50.
    zigzag = [10.0 + (i % 2) for i in range(30)]
    assert t.rsi(zigzag, 14)[-1] == pytest.approx(50, abs=4)


def test_total_returns_include_dividends_and_splits() -> None:
    # 2-for-1 split on day 2 and a $1 dividend on day 3.
    closes = [100.0, 50.0, 51.0]
    returns = t.total_returns(closes, [0, 0, 1.0], [1, 2.0, 1])
    assert returns == pytest.approx([0.0, 52 / 50 - 1])
    assert t.growth_index(returns)[-1] == pytest.approx(1.04)


def test_drawdown_volatility_correlation_and_beta() -> None:
    index = [1.0, 1.2, 0.9, 1.0, 1.3]
    assert t.drawdowns(index) == pytest.approx([0, 0, -0.25, -1 / 6, 0])
    assert t.max_drawdown(index) == pytest.approx(-0.25)
    market = [0.01, -0.02, 0.015, 0.0, 0.005]
    asset = [2 * r for r in market]
    assert t.beta(asset, market) == pytest.approx(2.0)
    assert t.correlation(asset, market) == pytest.approx(1.0)
    assert t.annualised_volatility([0.01, -0.01] * 50) == pytest.approx(
        0.01 * math.sqrt(100 / 99) * math.sqrt(252), rel=1e-6
    )


def test_macd_signal_starts_after_slow_window() -> None:
    values = [100 + math.sin(i / 5) * 3 for i in range(80)]
    result = t.macd(values)
    first_line = next(i for i, v in enumerate(result.line) if v is not None)
    first_signal = next(i for i, v in enumerate(result.signal) if v is not None)
    assert first_line == 25 and first_signal == 25 + 8
    assert result.histogram[-1] == pytest.approx(result.line[-1] - result.signal[-1])  # type: ignore[operator]


# --- Providers ----------------------------------------------------------------------------


def test_fred_observations_skip_missing_values() -> None:
    payload = {
        "observations": [
            {
                "realtime_start": "2024-02-02",
                "realtime_end": "9999-12-31",
                "date": "2024-01-01",
                "value": "3.7",
            },
            {
                "realtime_start": "2024-02-02",
                "realtime_end": "9999-12-31",
                "date": "2024-01-02",
                "value": ".",
            },
            {
                "realtime_start": "bad",
                "realtime_end": "9999-12-31",
                "date": "2024-01-03",
                "value": "1",
            },
        ]
    }
    vintages, rejected = parse_observations(payload)
    assert [v.value for v in vintages] == [3.7] and rejected == 1


def test_macro_availability_uses_vintages_or_a_conservative_lag() -> None:
    v = Vintage
    vintages = [
        # Released 2024-02-02, revised 2024-03-08.
        v(date(2024, 1, 1), date(2024, 2, 2), date(2024, 3, 7), 3.7),
        v(date(2024, 1, 1), date(2024, 3, 8), date(9999, 12, 31), 3.8),
        # ALFRED history starts in 2015 for a 2010 observation: use date + lag instead.
        v(date(2010, 1, 1), date(2015, 6, 1), date(9999, 12, 31), 9.8),
    ]
    paired = {(x.observation_date, x.value): d for x, d in availability(vintages, 40)}
    assert paired[(date(2024, 1, 1), 3.7)] == date(2024, 2, 2)
    assert paired[(date(2024, 1, 1), 3.8)] == date(2024, 3, 8)
    assert paired[(date(2010, 1, 1), 9.8)] == date(2010, 2, 10)


def _macro(rows: dict[str, list[tuple[date, date, float]]]) -> MacroHistory:
    history = MacroHistory.__new__(MacroHistory)
    history._rows = defaultdict(list, {k: sorted(v) for k, v in rows.items()})
    return history


def test_macro_history_answers_what_was_known_on_a_date() -> None:
    history = _macro(
        {
            "UNRATE": [
                (date(2024, 2, 2), date(2024, 1, 1), 3.7),
                (date(2024, 3, 8), date(2024, 1, 1), 3.8),
                (date(2024, 3, 8), date(2024, 2, 1), 3.9),
            ]
        }
    )
    assert history.latest("UNRATE", date(2024, 2, 1)) is None
    assert history.latest("UNRATE", date(2024, 2, 15)).value == 3.7  # type: ignore[union-attr]
    on_march_8 = history.known("UNRATE", date(2024, 3, 8))
    assert on_march_8[date(2024, 1, 1)].value == 3.8  # the revision replaced 3.7
    assert history.change("UNRATE", date(2024, 3, 8), 1) == pytest.approx(0.1)


def test_openfigi_prefers_the_us_composite_listing() -> None:
    payload = [
        {
            "data": [
                {"figi": "BBG001", "ticker": "AAPL", "exchCode": "UW", "name": "APPLE INC"},
                {
                    "figi": "BBG000B9XRY4",
                    "compositeFIGI": "BBG000B9XRY4",
                    "ticker": "AAPL",
                    "exchCode": "US",
                    "name": "APPLE INC",
                    "securityType": "Common Stock",
                },
            ]
        },
        {"warning": "No identifier found."},
    ]
    matches = parse_mapping(["037833100", "000000000"], payload)
    assert (matches[0].ticker, matches[0].exch_code, matches[0].figi) == (
        "AAPL",
        "US",
        "BBG000B9XRY4",
    )
    assert matches[1].figi is None


def test_openfigi_client_batches_and_waits_on_rate_limit() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(json.loads(request.content)))
        if len(calls) == 1:
            return httpx.Response(429, headers={"ratelimit-reset": "3"})
        return httpx.Response(
            200, json=[{"data": [{"figi": "F", "ticker": "X", "exchCode": "US"}]}]
        )

    client = OpenFigiClient(
        None, httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append
    )
    assert client.batch_size == 10
    result = client.map_cusips(["123456789"])
    assert result.data[0].ticker == "X" and calls == [1, 1]
    assert 4.0 in slept  # waited ratelimit-reset + 1 s


def test_frames_and_crypto_rows_parse() -> None:
    facts, rejected = parse_frame(
        {
            "data": [
                {
                    "accn": "a",
                    "cik": 320193,
                    "entityName": "Apple Inc.",
                    "end": "2024-09-28",
                    "val": 391035000000,
                },
                {"cik": "x"},
            ]
        }
    )
    assert facts[0].cik == 320193 and facts[0].value == 391035000000 and rejected == 1
    bar = parse_crypto_bar(
        {
            "date": "2024-04-20T00:00:00+00:00",
            "open": 63800,
            "high": 65400,
            "low": 63100,
            "close": 64900,
            "volume": 12000.5,
            "volumeNotional": 778832450.0,
            "tradesDone": 900000.0,
        }
    )
    assert (
        bar.trade_date == date(2024, 4, 20)
        and bar.close == Decimal("64900")
        and bar.trades == 900000
    )


# --- 13F ----------------------------------------------------------------------------------


def _zip(tables: dict[str, list[list[str]]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, rows in tables.items():
            archive.writestr(name, "\n".join("\t".join(r) for r in rows) + "\n")
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_13f_amendments_options_and_value_units() -> None:
    archive = _zip(
        {
            "SUBMISSION.tsv": [
                ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"],
                ["A1", "10-AUG-2026", "13F-HR", "1", "30-JUN-2026"],
                ["A2", "20-AUG-2026", "13F-HR/A", "1", "30-JUN-2026"],  # restatement replaces A1
                ["B1", "11-AUG-2026", "13F-HR", "2", "30-JUN-2026"],
                ["B2", "21-AUG-2026", "13F-HR/A", "2", "30-JUN-2026"],  # adds new holdings
                ["C1", "12-AUG-2026", "13F-HR", "3", "31-DEC-2001"],  # late filing, other period
            ],
            "COVERPAGE.tsv": [
                ["ACCESSION_NUMBER", "ISAMENDMENT", "AMENDMENTTYPE", "FILINGMANAGER_NAME"],
                ["A1", "N", "", "Alpha"],
                ["A2", "Y", "RESTATEMENT", "Alpha"],
                ["B1", "N", "", "Beta"],
                ["B2", "Y", "NEW HOLDINGS", "Beta"],
                ["C1", "N", "", "Gamma"],
            ],
            "INFOTABLE.tsv": [
                [
                    "ACCESSION_NUMBER",
                    "NAMEOFISSUER",
                    "CUSIP",
                    "VALUE",
                    "SSHPRNAMT",
                    "SSHPRNAMTTYPE",
                    "PUTCALL",
                ],
                ["A1", "APPLE", "037833100", "999", "9", "SH", ""],
                ["A2", "APPLE", "037833100", "2000", "10", "SH", ""],
                ["A2", "APPLE", "037833100", "500", "5", "SH", "Put"],
                ["B1", "APPLE", "037833100", "1000", "5", "SH", ""],
                ["B2", "APPLE", "037833100", "400", "2", "SH", ""],
                ["C1", "APPLE", "037833100", "7", "1", "SH", ""],
            ],
        }
    )
    period, filings = parse_filings(archive)
    assert period == date(2026, 6, 30)
    assert set(filings) == {"A2", "B1", "B2"}
    totals, _, positions, rejected = read_holdings(archive, filings, {"037833100"})
    assert totals["037833100"] == 3400 and rejected == 0
    assert positions[("037833100", 1)].shares == 10
    assert positions[("037833100", 2)].value == 1400


def test_13f_values_before_2023_are_in_thousands() -> None:
    filing = Filing("X", 1, date(2022, 8, 1), date(2022, 6, 30), "original")
    assert select_filings([filing]) == {"X": filing}
    archive = _zip(
        {
            "INFOTABLE.tsv": [
                [
                    "ACCESSION_NUMBER",
                    "NAMEOFISSUER",
                    "CUSIP",
                    "VALUE",
                    "SSHPRNAMT",
                    "SSHPRNAMTTYPE",
                    "PUTCALL",
                ],
                ["X", "APPLE", "037833100", "15", "100", "SH", ""],
            ]
        }
    )
    totals, _, _, _ = read_holdings(archive, {"X": filing}, None)
    assert totals["037833100"] == 15_000


# --- Universe -----------------------------------------------------------------------------


def test_month_ends() -> None:
    assert month_ends(date(2024, 1, 15), date(2024, 4, 29)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 31),
    ]


def test_revenue_as_of_uses_only_filed_values_and_concept_priority() -> None:
    fy = date(2023, 12, 31)
    timeline = [
        AnnualRevenue(date(2022, 12, 31), date(2023, 2, 20), Decimal(90), "Revenues"),
        AnnualRevenue(fy, date(2024, 2, 20), Decimal(100), "Revenues"),
        AnnualRevenue(
            fy,
            date(2024, 2, 20),
            Decimal(99),
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ),
        AnnualRevenue(
            fy, date(2024, 6, 1), Decimal(98), "RevenueFromContractWithCustomerExcludingAssessedTax"
        ),  # restated
    ]
    assert revenue_as_of(timeline, date(2024, 1, 31)).value == 90  # type: ignore[union-attr]
    assert revenue_as_of(timeline, date(2024, 3, 31)).value == 99  # type: ignore[union-attr]
    assert revenue_as_of(timeline, date(2024, 6, 30)).value == 98  # type: ignore[union-attr]
    assert revenue_as_of(timeline, date(2026, 1, 31)) is None  # stopped filing


# --- Features and leakage -----------------------------------------------------------------


def _series(start: date, days: int, drift: float, seed: int) -> ReturnSeries:
    rng = np.random.default_rng(seed)
    dates, closes = [], []
    day, price = start, 100.0
    while len(dates) < days:
        if day.weekday() < 5:
            dates.append(day)
            closes.append(price)
            price *= 1 + drift + rng.normal(0, 0.01)
        day += timedelta(days=1)
    returns = t.total_returns(closes)
    return ReturnSeries(dates, closes, returns)


def _inputs(cut: date | None) -> tuple[CompanyInputs, MarketInputs]:
    facts, _ = parse_company_facts(companyfacts(), TRACKED_UNITS, ACCEPTED_FORMS)
    prices = _series(date(2022, 1, 3), 900, 0.0004, 1)
    spy = _series(date(2022, 1, 3), 900, 0.0003, 2)
    macro = {
        "DGS10": [
            (
                date(2024, 3, 1) + timedelta(days=i),
                date(2024, 2, 29) + timedelta(days=i),
                4.0 + i / 100,
            )
            for i in range(60)
        ],
        "UNRATE": [
            (date(2024, 2, 2), date(2024, 1, 1), 3.7),
            (date(2024, 4, 5), date(2024, 3, 1), 3.9),
        ],
    }
    if cut is not None:
        facts = [f for f in facts if f.filed_date <= cut]
        prices = prices.as_of(cut)
        spy = spy.as_of(cut)
        macro = {k: [r for r in v if r[0] <= cut] for k, v in macro.items()}
    points = [
        PricePoint(d, Decimal(str(c)), None)
        for d, c in zip(prices.dates, prices.closes, strict=True)
    ]
    return CompanyInputs(facts, prices, points), MarketInputs(spy, _macro(macro))


@pytest.mark.parametrize("day", [date(2023, 12, 29), date(2024, 3, 15), date(2024, 11, 29)])
def test_features_use_only_data_available_on_the_date(day: date) -> None:
    """The leakage test: full histories and histories cut at `day` give identical features."""
    full = compute_features(*_inputs(None), day)
    cut = compute_features(*_inputs(day), day)
    assert full is not None and cut is not None
    assert full == cut
    assert full[1] <= day


def test_features_change_once_new_filings_are_public() -> None:
    before = compute_features(*_inputs(None), date(2024, 10, 31))
    after = compute_features(*_inputs(None), date(2024, 11, 29))  # FY2024 10-K filed 2024-11-01
    assert before is not None and after is not None
    assert before[0]["revenue_growth_yoy"] != after[0]["revenue_growth_yoy"]


def test_features_need_a_recent_price_and_a_year_of_history() -> None:
    company, market = _inputs(None)
    assert compute_features(company, market, date(2022, 6, 30)) is None  # under a year of bars
    assert compute_features(company, market, date(2030, 1, 31)) is None  # stale price


# --- Factors ------------------------------------------------------------------------------


def test_zscores_winsorise_and_skip_missing() -> None:
    values: list[float | None] = [float(i) for i in range(200)] + [10_000.0, None]
    z = zscores(values)
    assert z[-1] is None
    # Unclipped, the outlier would sit about 14 standard deviations out.
    assert z[-2] is not None and z[-2] < 2.0
    assert z[-2] == pytest.approx(z[-3], abs=0.1)  # clipped to the 99th percentile


def test_factor_scores_flip_sign_where_lower_is_better() -> None:
    ids = list(range(30))
    features = [{"vol_3m": 0.1 + i / 100, "beta_1y": 0.5 + i / 50} for i in ids]
    scores = {(c, f): (s, p) for c, f, s, p, _ in score_date(ids, features)}
    assert scores[(0, "low_volatility")][1] == 1.0  # least volatile ranks highest
    assert scores[(29, "low_volatility")][0] < 0
    assert (0, "value") not in scores  # no value inputs at all


# --- Modeling -----------------------------------------------------------------------------


def test_forward_excess_uses_the_following_bars() -> None:
    dates = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    stock = ReturnSeries(
        dates, [100, 101, 102, 103, 110], t.total_returns([100, 101, 102, 103, 110])
    )
    spy = ReturnSeries(dates, [10, 10, 10, 10.5, 11], t.total_returns([10, 10, 10, 10.5, 11]))
    excess, end = modeling.forward_excess(stock, spy, date(2024, 1, 3), 2)  # type: ignore[misc]
    assert end == date(2024, 1, 5)
    assert excess == pytest.approx(103 / 101 - 1 - 0.05)
    assert modeling.forward_excess(stock, spy, date(2024, 1, 5), 2) is None  # not known yet


def test_calibrator_choice_is_time_separated() -> None:
    rng = np.random.default_rng(3)
    p = rng.uniform(0.2, 0.8, 4000)
    y = (rng.uniform(size=4000) < 0.5 + (p - 0.5) * 0.3).astype(float)  # overconfident scores
    days = [date(2020, 1, 1) + timedelta(days=i // 20) for i in range(4000)]
    calibrator, info = modeling.choose_calibrator(p, y, days)
    assert info["chosen"] in {"platt", "isotonic"}
    assert info["brier_by_method"][info["chosen"]] < info["brier_by_method"]["none"]
    calibrated = calibrator.apply(p)
    assert np.ptp(calibrated) < np.ptp(p)  # pulled toward the base rate


def test_rank_ic_and_decile_spread() -> None:
    days = [date(2024, 1, 31)] * 50 + [date(2024, 2, 29)] * 50
    score = np.array(list(range(50)) * 2, dtype=float)
    excess = score / 100
    ic = modeling.rank_ic(days, score, excess)
    assert ic["mean"] == pytest.approx(1.0) and ic["dates"] == 2
    assert modeling.decile_spread(days, score, excess) == pytest.approx((47 - 2) / 100)


def _synthetic_dataset(n_dates: int = 96, per_date: int = 120) -> modeling.Dataset:
    rng = np.random.default_rng(5)
    as_of, ends, X, excess = [], [], [], []
    day = date(2014, 1, 31)
    for _ in range(n_dates):
        signal = rng.uniform(size=per_date)
        noise = rng.normal(0, 0.05, per_date)
        for i in range(per_date):
            as_of.append(day)
            ends.append(day + timedelta(days=30))
            X.append([signal[i], rng.uniform()])
            excess.append(0.1 * (signal[i] - 0.5) + noise[i])
        day = (day + timedelta(days=40)).replace(day=1) - timedelta(days=1)
    n = len(as_of)
    return modeling.Dataset(
        21,
        ["signal", "noise"],
        as_of,
        list(range(n)),
        np.array(X),
        [{} for _ in range(n)],
        np.array(excess),
        ends,
        as_of,
        ["Tech"] * n,
        ["Uptrend, calm"] * n,
    )


def test_walk_forward_learns_a_real_signal_without_peeking() -> None:
    ds = _synthetic_dataset()
    folds, summary = modeling.walk_forward(ds)
    assert folds and summary
    for fold in folds:
        # Every training label ended before the embargo preceding the test year.
        assert fold.train_rows < sum(1 for d in ds.as_of if d.year < fold.year)
    assert summary["model"]["auc"] > 0.6
    assert summary["model"]["rank_ic"]["mean"] > 0.1
    assert summary["baseline"]["auc"] > 0.6


def test_walk_forward_finds_nothing_in_noise() -> None:
    ds = _synthetic_dataset()
    ds = replace(ds, excess=np.random.default_rng(9).normal(0, 0.05, len(ds.excess)))
    _, summary = modeling.walk_forward(ds)
    assert abs(summary["model"]["auc"] - 0.5) < 0.05


# --- Regime and Bitcoin -------------------------------------------------------------------


def test_regime_rules() -> None:
    calm_up = _series(date(2015, 1, 5), 1500, 0.0008, 4)
    regime = classify(calm_up, None, calm_up.dates[-1])
    assert regime is not None and regime.label.startswith("Uptrend")
    assert classify(calm_up, None, calm_up.dates[100]) is None  # not enough history
    # A volatile decline: alternating −8% and +4% days.
    shocks = [-0.08 if i % 2 else 0.04 for i in range(1, 60)]
    crash_closes = list(calm_up.closes)
    for r in shocks:
        crash_closes.append(crash_closes[-1] * (1 + r))
    crash = ReturnSeries(
        calm_up.dates + [calm_up.dates[-1] + timedelta(days=i) for i in range(1, 60)],
        crash_closes,
        calm_up.returns + shocks,
    )
    stressed = classify(crash, None, crash.dates[-1])
    assert stressed is not None and stressed.label == "Downtrend, stressed"


def test_halving_cycles_and_aligned_returns() -> None:
    start = date(2016, 7, 9)
    dates = [start + timedelta(days=i) for i in range(800)]
    closes = [100.0 * (1 + i / 100) if i <= 500 else 600.0 * 0.999 ** (i - 500) for i in range(800)]
    cycles = bitcoin.halving_cycles(bitcoin.Daily(dates, closes), today=dates[-1])
    cycle = cycles[0]
    assert cycle.halving == start and cycle.days_to_peak == 500
    assert cycle.peak_multiple == pytest.approx(6.0)
    assert cycle.return_1y == pytest.approx(3.65 + 1 - 1, rel=1e-6)
    btc = bitcoin.Daily([date(2024, 1, d) for d in (5, 6, 7, 8)], [100, 101, 102, 103])
    spy = bitcoin.Daily([date(2024, 1, d) for d in (5, 8)], [10, 11])
    dates2, rb, rs = bitcoin.aligned_returns(btc, spy)
    assert (
        dates2 == [date(2024, 1, 8)] and rb == pytest.approx([0.03]) and rs == pytest.approx([0.1])
    )


def test_last_complete_day_skips_weekends() -> None:
    assert last_complete_day(date(2026, 10, 5)) == date(2026, 10, 2)  # Monday → Friday


def test_13f_positions_with_impossible_prices_are_dropped() -> None:
    positions = {("084670702", i): Position(Decimal(100), Decimal(50_000)) for i in range(10)}
    positions[("084670702", 99)] = Position(Decimal(368_743), Decimal(264_600_000_000))
    kept, dropped = drop_price_outliers(positions)
    assert dropped == 1 and ("084670702", 99) not in kept and len(kept) == 10
