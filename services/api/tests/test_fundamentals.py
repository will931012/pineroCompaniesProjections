from datetime import date
from decimal import Decimal

import pytest

from app.fundamentals.concepts import ACCEPTED_FORMS
from app.fundamentals.metrics import metric_series
from app.fundamentals.service import TRACKED_UNITS
from app.fundamentals.snapshot import PricePoint, compute_company_metrics
from app.fundamentals.statements import (
    build_statements,
    latest_shares_outstanding,
    shares_for_market_cap,
    split_events,
)
from app.providers.sec_edgar import FactObservation, parse_company_facts
from tests.fixtures_companyfacts import companyfacts

REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.fixture(scope="module")
def facts() -> list[FactObservation]:
    observations, _ = parse_company_facts(companyfacts(), TRACKED_UNITS, ACCEPTED_FORMS)
    return observations


def test_parse_keeps_each_value_once_at_its_first_public_date() -> None:
    observations, rejected = parse_company_facts(companyfacts(), TRACKED_UNITS, ACCEPTED_FORMS)
    fy23 = [o for o in observations if o.concept == REVENUE and o.period_end == date(2023, 9, 30)]

    # 1000 (original) and 990 (restated; also repeated in the FY2024 10-K) → two rows.
    assert sorted((o.value, o.filed_date) for o in fy23) == [
        (Decimal("990"), date(2024, 1, 15)),
        (Decimal("1000"), date(2023, 11, 3)),
    ]
    assert not any(o.form == "8-K" for o in observations)
    assert rejected == 1  # the malformed cash value


def test_annual_statements_use_latest_known_restatement(facts) -> None:  # type: ignore[no-untyped-def]
    annual = build_statements(facts, "annual")

    assert [p.key for p in annual.periods] == ["FY2023", "FY2024"]
    assert annual.get("revenue", "FY2023") == Decimal("990")
    assert annual.get("revenue", "FY2024") == Decimal("1200")
    cell = annual.cells["revenue"]["FY2024"]
    assert cell.concept == REVENUE and cell.accession == "0000999001-24-000040"
    assert cell.derivation is None


def test_point_in_time_excludes_later_filings(facts) -> None:  # type: ignore[no-untyped-def]
    before_restatement = build_statements(facts, "annual", as_of=date(2023, 12, 31))

    assert [p.key for p in before_restatement.periods] == ["FY2023"]
    assert before_restatement.get("revenue", "FY2023") == Decimal("1000")


@pytest.mark.parametrize("as_of", [date(2023, 11, 3), date(2024, 2, 2), date(2024, 8, 2)])
def test_no_value_is_used_before_it_was_public(facts, as_of: date) -> None:  # type: ignore[no-untyped-def]
    for period_type in ("annual", "quarterly"):
        statements = build_statements(facts, period_type, as_of=as_of)  # type: ignore[arg-type]
        for cells in statements.cells.values():
            assert all(cell.filed_date <= as_of for cell in cells.values())


def test_quarters_are_derived_from_year_to_date_values(facts) -> None:  # type: ignore[no-untyped-def]
    quarterly = build_statements(facts, "quarterly")

    assert [p.label for p in quarterly.periods] == [
        "Q1 FY2024",
        "Q2 FY2024",
        "Q3 FY2024",
        "Q4 FY2024",
    ]
    revenue = quarterly.cells["revenue"]
    assert [revenue[k].value for k in ("FY2024-Q1", "FY2024-Q2", "FY2024-Q3", "FY2024-Q4")] == [
        Decimal(280),
        Decimal(300),
        Decimal(290),
        Decimal(330),
    ]
    assert revenue["FY2024-Q2"].derivation is None  # reported three-month value
    assert revenue["FY2024-Q4"].derivation == "FY − 9M YTD"
    ocf = quarterly.cells["operating_cash_flow"]
    assert [ocf[f"FY2024-Q{q}"].value for q in range(1, 5)] == [60, 70, 80, 90]
    assert ocf["FY2024-Q3"].derivation == "9M YTD − 6M YTD"
    assert quarterly.get("cash", "FY2024-Q2") == Decimal(320)


def test_quarter_start_dates_follow_previous_quarter_end(facts) -> None:  # type: ignore[no-untyped-def]
    periods = build_statements(facts, "quarterly").periods

    assert [(p.start, p.end) for p in periods][1] == (date(2023, 12, 31), date(2024, 3, 30))


def test_annual_metrics_match_hand_calculations(facts) -> None:  # type: ignore[no-untyped-def]
    series = metric_series(build_statements(facts, "annual"))
    fy = "FY2024"

    assert series["revenue_growth_yoy"][fy] == Decimal(1200) / Decimal(990) - 1
    assert series["roe"][fy] == Decimal(150) / Decimal(900)
    # NOPAT = 200 × (1 − 36/180) = 160; invested capital (800+500−300, 1000+500−350) avg 1075
    assert series["roic"][fy] == Decimal(160) / Decimal(1075)
    assert series["fcf_margin"][fy] == Decimal(250) / Decimal(1200)
    assert series["current_ratio"][fy] == Decimal("1.5")
    assert series["debt_to_ebitda"][fy] == Decimal(500) / Decimal(230)
    assert series["eps_growth_yoy"][fy] == Decimal("0.25")
    # No prior year exists for FY2023, so growth there is undefined rather than zero.
    assert "FY2023" not in series["revenue_growth_yoy"]


def test_multi_class_share_counts_are_summed(facts) -> None:  # type: ignore[no-untyped-def]
    shares = latest_shares_outstanding(facts, None)

    assert shares is not None
    assert (shares.value, shares.classes, shares.as_of) == (Decimal(60), 2, date(2024, 10, 18))
    assert latest_shares_outstanding(facts, date(2024, 1, 1)).value == Decimal(58)  # type: ignore[union-attr]


def _prices(days: int, start_price: float = 100.0) -> list[PricePoint]:
    base = date(2024, 11, 1).toordinal() - days
    return [
        PricePoint(
            date.fromordinal(base + i),
            Decimal(str(start_price + i * 0.1)),
            Decimal(str(start_price + i * 0.1)),
        )
        for i in range(days + 1)
    ]


def test_snapshot_uses_ttm_and_price_with_stated_basis(facts) -> None:  # type: ignore[no-untyped-def]
    prices = _prices(400)
    metrics = {m.key: m for m in compute_company_metrics(facts, prices)}
    last_close = prices[-1].close

    assert metrics["revenue"].value == Decimal(1200)
    assert metrics["revenue"].basis == "TTM Q4 FY2024"
    assert metrics["market_cap"].value == last_close * 60
    assert "2 share classes" in metrics["market_cap"].basis
    assert metrics["pe_ratio"].value == last_close * 60 / 150
    assert metrics["enterprise_value"].value == last_close * 60 + 500 - 350
    assert metrics["momentum_12m"].value > 0
    assert metrics["volatility_1y"].value >= 0
    # No prior TTM span exists, so growth falls back to fiscal years and says so.
    assert metrics["revenue_growth_yoy"].basis == "FY2024"
    assert metrics["revenue_growth_yoy"].value == Decimal(1200) / Decimal(990) - 1
    # Net income is only reported annually, so P/E states the fiscal-year basis.
    assert metrics["pe_ratio"].basis.endswith("; FY2024")


def test_snapshot_without_prices_has_no_market_metrics(facts) -> None:  # type: ignore[no-untyped-def]
    keys = {m.key for m in compute_company_metrics(facts, [])}

    assert "market_cap" not in keys and "pe_ratio" not in keys and "momentum_1m" not in keys
    assert {"revenue", "roic", "current_ratio"} <= keys


def test_short_price_history_has_no_long_horizon_metrics(facts) -> None:  # type: ignore[no-untyped-def]
    keys = {m.key for m in compute_company_metrics(facts, _prices(40))}

    assert "momentum_1m" in keys
    assert "momentum_6m" not in keys and "volatility_1y" not in keys


def _fact(
    concept: str,
    start: str | None,
    end: str,
    value: int,
    accession: str,
    filed: str,
    fy: int,
    fp: str,
    form: str = "10-Q",
) -> FactObservation:
    return FactObservation(
        taxonomy="us-gaap",
        concept=concept,
        unit="USD",
        period_start=date.fromisoformat(start) if start else None,
        period_end=date.fromisoformat(end),
        value=Decimal(value),
        fiscal_year=fy,
        fiscal_period=fp,
        form=form,
        accession=accession,
        filed_date=date.fromisoformat(filed),
    )


# Q1 FY2025 10-Q, filed after the FY2024 10-K: the fiscal year is still in progress.
Q1_FY25 = [
    _fact(
        REVENUE, "2024-09-29", "2024-12-28", 310, "0000999001-25-000005", "2025-01-31", 2025, "Q1"
    ),
    _fact(
        "NetCashProvidedByUsedInOperatingActivities",
        "2024-09-29",
        "2024-12-28",
        95,
        "0000999001-25-000005",
        "2025-01-31",
        2025,
        "Q1",
    ),
]


def test_in_progress_year_adds_quarters_after_the_last_10k(facts) -> None:  # type: ignore[no-untyped-def]
    quarterly = build_statements([*facts, *Q1_FY25], "quarterly")
    annual = build_statements([*facts, *Q1_FY25], "annual")

    assert [p.label for p in quarterly.periods][-2:] == ["Q4 FY2024", "Q1 FY2025"]
    assert quarterly.get("revenue", "FY2025-Q1") == Decimal(310)
    # An in-progress year never appears as an annual period.
    assert [p.key for p in annual.periods] == ["FY2023", "FY2024"]


def test_in_progress_quarters_are_point_in_time(facts) -> None:  # type: ignore[no-untyped-def]
    # Before the FY2024 10-K, the three FY2024 10-Qs form the in-progress year.
    quarterly = build_statements(facts, "quarterly", as_of=date(2024, 8, 2))

    assert [p.label for p in quarterly.periods] == ["Q1 FY2024", "Q2 FY2024", "Q3 FY2024"]
    ocf = quarterly.cells["operating_cash_flow"]
    assert ocf["FY2024-Q3"].value == Decimal(80)
    assert ocf["FY2024-Q3"].derivation == "9M YTD − 6M YTD"


def test_ttm_includes_the_latest_10q(facts) -> None:  # type: ignore[no-untyped-def]
    metrics = {m.key: m for m in compute_company_metrics([*facts, *Q1_FY25], [])}

    assert metrics["revenue"].basis == "TTM Q1 FY2025"
    assert metrics["revenue"].value == Decimal(300 + 290 + 330 + 310)
    assert metrics["revenue"].available_date == date(2025, 1, 31)


def test_comparative_years_are_not_labelled_with_the_filings_year(facts) -> None:  # type: ignore[no-untyped-def]
    # The issuer's first XBRL 10-K (FY2023) also reports FY2022 as a comparative; SEC tags
    # that value fy=2023 because fy describes the filing.
    fy22 = _fact(
        REVENUE,
        "2021-10-01",
        "2022-09-30",
        900,
        "0000999001-23-000010",
        "2023-11-03",
        2023,
        "FY",
        form="10-K",
    )
    annual = build_statements([*facts, fy22], "annual")

    assert [p.key for p in annual.periods] == ["FY2022", "FY2023", "FY2024"]
    assert annual.get("revenue", "FY2022") == Decimal(900)
    assert annual.get("revenue", "FY2023") == Decimal(990)


def _shares_fact(
    concept: str, start: str | None, end: str, value: int, filed: str
) -> FactObservation:
    fact = _fact(concept, start, end, value, "0000999001-26-000001", filed, 2026, "Q1")
    return FactObservation(**{**fact.__dict__, "unit": "shares"})


def test_stale_cover_page_count_is_not_used_for_market_cap(facts) -> None:  # type: ignore[no-untyped-def]
    # The only cover-page count is from 2024-10-18; a price in 2026 is too far from it.
    assert shares_for_market_cap(facts, None, date(2026, 6, 30)) is None
    keys = {m.key for m in compute_company_metrics(facts, _prices(400, start_price=50.0))}
    assert "market_cap" in keys  # prices end 2024-11-01, so the count is current there


def test_market_cap_falls_back_to_balance_sheet_then_weighted_shares(facts) -> None:  # type: ignore[no-untyped-def]
    balance = _shares_fact("CommonStockSharesOutstanding", None, "2026-03-31", 55, "2026-04-30")
    weighted = _shares_fact(
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "2026-01-01",
        "2026-03-31",
        54,
        "2026-04-30",
    )

    count = shares_for_market_cap([*facts, balance, weighted], None, date(2026, 6, 30))
    assert count is not None and (count.value, count.source) == (Decimal(55), "balance-sheet")

    count = shares_for_market_cap([*facts, weighted], None, date(2026, 6, 30))
    assert count is not None and (count.value, count.source) == (
        Decimal(54),
        "weighted-average basic",
    )


def _weighted(start: str, end: str, value: int, accession: str, filed: str) -> FactObservation:
    fact = _fact(
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        start,
        end,
        value,
        accession,
        filed,
        int(filed[:4]),
        "FY",
        form="10-K",
    )
    return FactObservation(**{**fact.__dict__, "unit": "shares"})


# A 4-for-1 split first reflected in the FY2024 10-K: it restates FY2023 shares 100 → 400.
# FY2022 (only in the FY2023 10-K) and FY2023 EPS are never restated by a later filing.
SPLIT = [
    _weighted("2021-10-01", "2022-09-30", 98, "0000999001-23-000010", "2023-11-03"),
    _weighted("2022-10-01", "2023-09-30", 100, "0000999001-23-000010", "2023-11-03"),
    _weighted("2022-10-01", "2023-09-30", 400, "0000999001-24-000040", "2024-11-01"),
    _weighted("2023-10-01", "2024-09-28", 404, "0000999001-24-000040", "2024-11-01"),
]


def test_split_is_detected_from_restated_share_counts(facts) -> None:  # type: ignore[no-untyped-def]
    events = split_events([*facts, *SPLIT], None)

    assert [(e.effective, e.ratio) for e in events] == [(date(2024, 11, 1), Decimal(4))]
    # Ordinary restatements are not splits.
    assert split_events(facts, None) == []


def test_pre_split_values_are_restated_onto_the_new_basis(facts) -> None:  # type: ignore[no-untyped-def]
    annual = build_statements([*facts, *SPLIT], "annual")

    shares = annual.cells["shares_diluted"]
    assert shares["FY2022"].value == Decimal(392)
    assert shares["FY2022"].derivation == "Adjusted (split ×4 from 2024-11-01)"
    assert shares["FY2024"].derivation is None
    assert annual.get("eps_diluted", "FY2023") == Decimal("0.5")  # 2.00 before the split
    series = metric_series(annual)
    assert series["share_dilution_yoy"]["FY2024"] == Decimal(404) / Decimal(400) - 1
    assert series["share_dilution_yoy"]["FY2023"] == Decimal(400) / Decimal(392) - 1


def test_split_is_not_applied_before_it_was_public(facts) -> None:  # type: ignore[no-untyped-def]
    annual = build_statements([*facts, *SPLIT], "annual", as_of=date(2024, 6, 1))

    assert annual.get("eps_diluted", "FY2023") == Decimal("2.00")
    assert annual.cells["shares_diluted"]["FY2023"].derivation is None


def test_thousands_tagging_errors_are_not_splits(facts) -> None:  # type: ignore[no-untyped-def]
    # Some early filings tagged shares in thousands; a later ×1000 restatement is not a split
    # and must not rescale per-share history. (SEC's NVIDIA data has exactly this pattern.)
    scaled = [
        _weighted("2022-10-01", "2023-09-30", 100, "0000999001-23-000010", "2023-11-03"),
        _weighted("2022-10-01", "2023-09-30", 100_000, "0000999001-24-000040", "2024-11-01"),
    ]

    assert split_events([*facts, *scaled], None) == []
    assert build_statements([*facts, *scaled], "annual").get("eps_diluted", "FY2023") == Decimal(
        "2.00"
    )
