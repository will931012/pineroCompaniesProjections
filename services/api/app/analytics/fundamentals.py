"""Deterministic fundamental formulas. Pure functions over Decimal; None means undefined.

No function substitutes a default for a missing input. When a ratio's denominator is zero
or of the wrong sign for the ratio to be meaningful, the result is None.
"""

from decimal import Decimal

FORMULA_VERSION = "2026.09-1"
ZERO = Decimal(0)
ONE = Decimal(1)


def ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """numerator / denominator; None if either is missing or denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def margin(amount: Decimal | None, revenue: Decimal | None) -> Decimal | None:
    """amount / revenue; defined only for positive revenue."""
    if revenue is None or revenue <= 0:
        return None
    return ratio(amount, revenue)


def growth(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    """current / previous − 1; undefined when the base is missing or not positive."""
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - ONE


def cagr(end: Decimal | None, start: Decimal | None, years: int) -> Decimal | None:
    """(end / start)^(1/years) − 1; undefined unless both values are positive."""
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (ONE / Decimal(years)) - ONE


def average(first: Decimal | None, second: Decimal | None) -> Decimal | None:
    """Mean of opening and closing balances; requires both."""
    if first is None or second is None:
        return None
    return (first + second) / 2


def total(*parts: Decimal | None) -> Decimal | None:
    """Sum of the parts that are present; None when every part is missing."""
    present = [part for part in parts if part is not None]
    return sum(present, ZERO) if present else None


def difference(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a - b


def free_cash_flow(operating_cash_flow: Decimal | None, capex: Decimal | None) -> Decimal | None:
    """Operating cash flow − capital expenditures (capex reported as a positive outflow)."""
    return difference(operating_cash_flow, capex)


def ebitda(operating_income: Decimal | None, d_and_a: Decimal | None) -> Decimal | None:
    """Operating income + depreciation & amortization."""
    if operating_income is None or d_and_a is None:
        return None
    return operating_income + d_and_a


def effective_tax_rate(income_tax: Decimal | None, pretax_income: Decimal | None) -> Decimal | None:
    """Income tax / pre-tax income; only for positive pre-tax income and a rate in [0, 1]."""
    if pretax_income is None or pretax_income <= 0:
        return None
    rate = ratio(income_tax, pretax_income)
    if rate is None or rate < 0 or rate > 1:
        return None
    return rate


def nopat(operating_income: Decimal | None, tax_rate: Decimal | None) -> Decimal | None:
    """Operating income × (1 − effective tax rate)."""
    if operating_income is None or tax_rate is None:
        return None
    return operating_income * (ONE - tax_rate)


def invested_capital(
    equity: Decimal | None, debt: Decimal | None, cash: Decimal | None
) -> Decimal | None:
    """Shareholders' equity + total debt − cash."""
    if equity is None or debt is None or cash is None:
        return None
    return equity + debt - cash


def return_on(numerator: Decimal | None, average_base: Decimal | None) -> Decimal | None:
    """Return on an average balance; undefined for a non-positive base."""
    if average_base is None or average_base <= 0:
        return None
    return ratio(numerator, average_base)


def interest_coverage(
    operating_income: Decimal | None, interest_expense: Decimal | None
) -> Decimal | None:
    """Operating income / interest expense; undefined without positive interest expense."""
    if interest_expense is None or interest_expense <= 0:
        return None
    return ratio(operating_income, interest_expense)


def leverage_to_ebitda(debt: Decimal | None, ebitda_value: Decimal | None) -> Decimal | None:
    """Debt (or net debt) / EBITDA; undefined when EBITDA is not positive."""
    if ebitda_value is None or ebitda_value <= 0:
        return None
    return ratio(debt, ebitda_value)


def quick_assets(
    cash: Decimal | None,
    short_term_investments: Decimal | None,
    receivables: Decimal | None,
) -> Decimal | None:
    """Cash + short-term investments + receivables (cash is required)."""
    if cash is None:
        return None
    return total(cash, short_term_investments, receivables)


def yield_on(amount: Decimal | None, market_cap: Decimal | None) -> Decimal | None:
    """amount / market capitalisation."""
    if market_cap is None or market_cap <= 0:
        return None
    return ratio(amount, market_cap)


def earnings_multiple(market_cap: Decimal | None, earnings: Decimal | None) -> Decimal | None:
    """market value / earnings-type denominator; undefined for non-positive denominators."""
    if earnings is None or earnings <= 0:
        return None
    return ratio(market_cap, earnings)
