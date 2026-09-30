from decimal import Decimal as D

import pytest

from app.analytics import fundamentals as f


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (D(110), D(100), D("0.1")),
        (D(90), D(100), D("-0.1")),
        (D(5), D(0), None),
        (D(5), D(-5), None),
        (None, D(1), None),
    ],
)
def test_growth(current, previous, expected) -> None:  # type: ignore[no-untyped-def]
    assert f.growth(current, previous) == expected


def test_cagr_is_exact_for_perfect_powers() -> None:
    assert f.cagr(D("133.1"), D(100), 3).quantize(D("0.000001")) == D("0.100000")  # type: ignore[union-attr]
    assert f.cagr(D(-1), D(100), 3) is None
    assert f.cagr(D(100), D(0), 3) is None


def test_margin_requires_positive_revenue() -> None:
    assert f.margin(D(25), D(100)) == D("0.25")
    assert f.margin(D(25), D(0)) is None
    assert f.margin(D(25), D(-100)) is None


def test_effective_tax_rate_bounds() -> None:
    assert f.effective_tax_rate(D(21), D(100)) == D("0.21")
    assert f.effective_tax_rate(D(21), D(-100)) is None
    assert f.effective_tax_rate(D(-5), D(100)) is None
    assert f.effective_tax_rate(D(150), D(100)) is None


def test_returns_need_positive_average_base() -> None:
    assert f.return_on(D(10), f.average(D(80), D(120))) == D("0.1")
    assert f.return_on(D(10), f.average(None, D(120))) is None
    assert f.return_on(D(10), D(-50)) is None


def test_totals_and_differences_do_not_fill_missing_values() -> None:
    assert f.total(D(1), None, D(2)) == D(3)
    assert f.total(None, None) is None
    assert f.difference(D(5), None) is None
    assert f.free_cash_flow(D(300), D(50)) == D(250)
    assert f.ebitda(D(200), None) is None


def test_leverage_and_multiples_undefined_for_non_positive_denominators() -> None:
    assert f.leverage_to_ebitda(D(500), D(250)) == D(2)
    assert f.leverage_to_ebitda(D(500), D(-1)) is None
    assert f.interest_coverage(D(100), D(0)) is None
    assert f.earnings_multiple(D(1000), D(50)) == D(20)
    assert f.earnings_multiple(D(1000), D(-50)) is None
    assert f.yield_on(D(40), D(1000)) == D("0.04")


def test_quick_assets_requires_cash() -> None:
    assert f.quick_assets(D(10), None, D(5)) == D(15)
    assert f.quick_assets(None, D(10), D(5)) is None
