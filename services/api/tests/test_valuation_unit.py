"""Valuation engine checks against values computed by hand."""

import math
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.analytics.valuation import (
    DEFAULT_SCENARIOS,
    DcfAssumptions,
    DdmAssumptions,
    RimAssumptions,
    ValuationError,
    WaccInputs,
    apply_scenario,
    dcf,
    ddm,
    rim,
    sensitivity,
    value,
)
from app.fundamentals.concepts import ACCEPTED_FORMS
from app.fundamentals.service import TRACKED_UNITS
from app.fundamentals.snapshot import PricePoint
from app.fundamentals.statements import build_statements
from app.providers.sec_edgar import parse_company_facts
from app.providers.treasury import parse_yield_curve
from app.valuation.service import Latest, estimate_beta
from tests.fixtures_companyfacts import companyfacts

FLAT_DCF = DcfAssumptions(
    base_revenue=1000,
    growth=0.0,
    margin=0.2,
    long_run_margin=0.2,
    terminal_growth=0.0,
    tax_rate=0.25,
    sales_to_capital=2.0,
    discount_rate=0.10,
    terminal_roic=None,
    debt=200,
    cash=50,
    shares=10,
)


def test_wacc_weights_equity_and_after_tax_debt() -> None:
    inputs = WaccInputs(
        risk_free=0.04,
        beta=1.2,
        equity_risk_premium=0.05,
        pre_tax_cost_of_debt=0.06,
        tax_rate=0.25,
        equity_value=800,
        debt_value=200,
    )
    # ke = 4% + 1.2 × 5% = 10%; WACC = 0.8 × 10% + 0.2 × 6% × 0.75 = 8.9%
    assert inputs.cost_of_equity() == pytest.approx(0.10)
    assert inputs.wacc() == pytest.approx(0.089)
    with pytest.raises(ValuationError):
        replace(inputs, equity_value=0, debt_value=0).wacc()


def test_zero_growth_dcf_is_a_perpetuity() -> None:
    result = dcf(FLAT_DCF)
    # NOPAT 1000 × 20% × 75% = 150 a year with no reinvestment: EV = 150 / 10% = 1500.
    assert result.enterprise_value == pytest.approx(1500)
    assert result.equity_value == pytest.approx(1350)  # − debt 200 + cash 50
    assert result.per_share == pytest.approx(135)
    assert len(result.years) == 10
    assert all(row.reinvestment == pytest.approx(0) for row in result.years)
    assert result.terminal_share == pytest.approx(result.pv_terminal / 1500)


def test_mid_year_convention_raises_value_by_half_a_year() -> None:
    end = dcf(FLAT_DCF)
    mid = dcf(replace(FLAT_DCF, mid_year=True))
    assert mid.pv_explicit == pytest.approx(end.pv_explicit * math.sqrt(1.10))


def test_terminal_value_with_ronic_equal_to_wacc_ignores_growth() -> None:
    result = dcf(replace(FLAT_DCF, growth=0.05, terminal_growth=0.03))
    revenue_11 = result.years[-1].revenue * 1.03  # type: ignore[operator]
    nopat_11 = revenue_11 * 0.2 * 0.75
    # FCFF11 = NOPAT11 × (1 − g/RONIC); with RONIC = r, TV = NOPAT11 / r.
    assert result.terminal_value == pytest.approx(nopat_11 / 0.10)


def test_growth_fades_to_terminal_rate_over_years_six_to_ten() -> None:
    rows = dcf(replace(FLAT_DCF, growth=0.10, terminal_growth=0.02, margin=0.3)).years
    assert [round(r.growth, 4) for r in rows] == [  # type: ignore[arg-type]
        0.10, 0.10, 0.10, 0.10, 0.10, 0.084, 0.068, 0.052, 0.036, 0.02,
    ]  # fmt: skip
    assert rows[5].margin == pytest.approx(0.3 - 0.1 * 0.2)
    assert rows[-1].margin == pytest.approx(0.2)


def test_operating_losses_are_not_taxed() -> None:
    result = dcf(replace(FLAT_DCF, margin=-0.1, long_run_margin=-0.1))
    assert result.years[0].nopat == pytest.approx(-100)


def test_discount_rate_must_exceed_terminal_growth() -> None:
    with pytest.raises(ValuationError, match="must exceed terminal growth"):
        dcf(replace(FLAT_DCF, discount_rate=0.03, terminal_growth=0.028))


def test_residual_income_equals_book_value_when_roe_equals_cost_of_equity() -> None:
    a = RimAssumptions(
        book_value=100,
        roe=0.10,
        long_run_roe=None,
        payout_ratio=0.4,
        cost_of_equity=0.10,
        terminal_growth=0.02,
        shares=4,
    )
    assert rim(a).equity_value == pytest.approx(100)
    assert rim(a).per_share == pytest.approx(25)


def test_residual_income_capitalises_a_permanent_excess_return() -> None:
    a = RimAssumptions(
        book_value=100,
        roe=0.15,
        long_run_roe=0.15,
        payout_ratio=1.0,
        cost_of_equity=0.10,
        terminal_growth=0.0,
        shares=1,
    )
    # Book stays at 100 (all earnings paid out); residual income 5 a year forever: 100 + 5/10%.
    assert rim(a).equity_value == pytest.approx(150)


def test_dividend_discount_matches_gordon_when_growth_is_constant() -> None:
    a = DdmAssumptions(
        dividend_per_share=2.0, growth=0.03, terminal_growth=0.03, cost_of_equity=0.08
    )
    assert ddm(a).per_share == pytest.approx(2 * 1.03 / (0.08 - 0.03))


def test_scenarios_order_bear_base_bull() -> None:
    base = replace(FLAT_DCF, growth=0.06, terminal_growth=0.025, discount_rate=0.09)
    values = {
        name: value(apply_scenario(base, shift)).per_share
        for name, shift in DEFAULT_SCENARIOS.items()
    }
    assert values["bear"] < values["base"] < values["bull"]  # type: ignore[operator]


def test_scenarios_shift_roe_for_residual_income() -> None:
    a = RimAssumptions(100, 0.12, None, 0.5, 0.10, 0.02, 1)
    bull = apply_scenario(a, DEFAULT_SCENARIOS["bull"])
    assert isinstance(bull, RimAssumptions)
    assert bull.roe == pytest.approx(0.14) and bull.cost_of_equity == pytest.approx(0.09)


def test_sensitivity_grid_centre_is_the_base_case_and_invalid_cells_are_empty() -> None:
    base = replace(FLAT_DCF, discount_rate=0.05, terminal_growth=0.04)
    rate_grid, driver_grid = sensitivity(base)
    assert rate_grid.rows == pytest.approx([0.04, 0.045, 0.05, 0.055, 0.06])
    assert rate_grid.values[2][2] == pytest.approx(dcf(base).per_share)
    # Discount rate 4% against terminal growth 5%: no finite value.
    assert rate_grid.values[0][4] is None
    assert driver_grid.row_label == "growth" and driver_grid.column_label == "margin"
    assert driver_grid.values[2][2] == pytest.approx(dcf(base).per_share)


def _prices(returns: list[float], start: float = 100.0) -> list[PricePoint]:
    day = date(2024, 1, 1)
    level = start
    points = [PricePoint(day, Decimal(str(level)), None)]
    for r in returns:
        day += timedelta(days=1)
        level *= math.exp(r)
        points.append(PricePoint(day, Decimal(str(round(level, 10))), None))
    return points


def test_beta_is_estimated_from_weekly_returns_and_blume_adjusted() -> None:
    market_returns = [0.01 * math.sin(i * 0.7) + 0.002 * ((i * 37) % 5 - 2) for i in range(400)]
    market = _prices(market_returns)
    stock = _prices([1.5 * r for r in market_returns])
    beta = estimate_beta(stock, market)
    assert beta is not None
    assert beta.raw == pytest.approx(1.5, abs=1e-4)
    assert beta.adjusted == pytest.approx(0.67 * 1.5 + 0.33, abs=1e-4)  # 1.335
    assert beta.observations == 80


def test_beta_needs_a_year_of_weekly_returns() -> None:
    market = _prices([0.01] * 100)
    assert estimate_beta(market, market) is None


def test_treasury_csv_is_parsed_to_decimals() -> None:
    text = (
        'Date,"1 Mo","3 Mo","10 Yr","30 Yr"\n'
        "09/30/2026,4.10,4.05,5.29,5.40\n"
        "09/29/2026,4.11,,5.31,N/A\n"
        "not a date,1,1,1,1\n"
    )
    observations, rejected = parse_yield_curve(text)
    ten_year = {o.observed_on: o.value for o in observations if o.tenor == "10 Yr"}
    assert ten_year == {
        date(2026, 9, 30): pytest.approx(0.0529),
        date(2026, 9, 29): pytest.approx(0.0531),
    }
    assert rejected == 2  # the bad date row and "N/A"


def test_latest_falls_back_to_the_fiscal_year_per_item() -> None:
    observations, _ = parse_company_facts(companyfacts(), TRACKED_UNITS, ACCEPTED_FORMS)
    latest = Latest(
        build_statements(observations, "annual"), build_statements(observations, "quarterly")
    )
    # Revenue is reported quarterly (TTM); operating income only in the 10-K.
    assert latest.item("revenue") == (1200.0, "TTM Q4 FY2024")
    assert latest.item("operating_income") == (200.0, "FY2024")
    found = latest.find("revenue", "operating_income")
    assert found is not None and found[1] == "FY2024"
    assert latest.item("goodwill") == (None, "TTM Q4 FY2024")
