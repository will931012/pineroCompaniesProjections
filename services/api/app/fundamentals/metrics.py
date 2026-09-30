"""Historical metric series computed from statements with the formulas in analytics."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.analytics import fundamentals as f
from app.fundamentals.statements import Period, StatementSet

Unit = Literal["percent", "ratio", "currency", "shares", "per_share"]
Category = Literal[
    "Growth",
    "Profitability",
    "Returns",
    "Liquidity",
    "Leverage",
    "Cash flow",
    "Shareholders",
]


class Context:
    """Values for one period plus the comparison periods a formula may need."""

    def __init__(self, statements: StatementSet, index: int) -> None:
        self.s = statements
        self.period = statements.periods[index]
        self._by_fq = {(p.fiscal_year, p.fiscal_quarter): p for p in statements.periods}
        self._previous = statements.periods[index - 1] if index > 0 else None
        self.annual = statements.period_type == "annual"

    def cur(self, item: str) -> Decimal | None:
        return self.s.get(item, self.period.key)

    def _at(self, period: Period | None, item: str) -> Decimal | None:
        return self.s.get(item, period.key) if period else None

    def year_ago(self, item: str, years: int = 1) -> Decimal | None:
        p = self._by_fq.get((self.period.fiscal_year - years, self.period.fiscal_quarter))
        return self._at(p, item)

    def previous(self, item: str) -> Decimal | None:
        """Immediately preceding period, only if contiguous (no missing fiscal period)."""
        prev = self._previous
        if prev is None:
            return None
        if self.annual:
            contiguous = prev.fiscal_year == self.period.fiscal_year - 1
        else:
            expected = (
                (self.period.fiscal_year, (self.period.fiscal_quarter or 1) - 1)
                if self.period.fiscal_quarter != 1
                else (self.period.fiscal_year - 1, 4)
            )
            contiguous = (prev.fiscal_year, prev.fiscal_quarter) == expected
        return self._at(prev, item) if contiguous else None

    # Composite values used by several formulas
    def debt(self, previous: bool = False) -> Decimal | None:
        get = self.previous if previous else self.cur
        return f.total(get("short_term_debt"), get("long_term_debt"))

    def fcf(self, when: str = "cur") -> Decimal | None:
        get = {"cur": self.cur, "year_ago": self.year_ago}[when]
        return f.free_cash_flow(get("operating_cash_flow"), get("capex"))

    def ebitda(self) -> Decimal | None:
        return f.ebitda(self.cur("operating_income"), self.cur("depreciation_amortization"))


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    category: Category
    unit: Unit
    formula: str
    compute: Callable[[Context], Decimal | None]
    annual_only: bool = False


def _roic(c: Context) -> Decimal | None:
    tax = f.effective_tax_rate(c.cur("income_tax"), c.cur("pretax_income"))
    ic_now = f.invested_capital(c.cur("total_equity"), c.debt(), c.cur("cash"))
    ic_before = f.invested_capital(
        c.previous("total_equity"), c.debt(previous=True), c.previous("cash")
    )
    return f.return_on(f.nopat(c.cur("operating_income"), tax), f.average(ic_before, ic_now))


def _working_capital(get: Callable[[str], Decimal | None]) -> Decimal | None:
    return f.difference(get("current_assets"), get("current_liabilities"))


METRICS: tuple[MetricDef, ...] = (
    # Growth
    MetricDef(
        "revenue_growth_yoy",
        "Revenue growth (YoY)",
        "Growth",
        "percent",
        "Revenue ÷ revenue in the same period a year earlier − 1",
        lambda c: f.growth(c.cur("revenue"), c.year_ago("revenue")),
    ),
    MetricDef(
        "revenue_growth_qoq",
        "Revenue growth (QoQ)",
        "Growth",
        "percent",
        "Revenue ÷ previous quarter's revenue − 1",
        lambda c: None if c.annual else f.growth(c.cur("revenue"), c.previous("revenue")),
    ),
    MetricDef(
        "revenue_cagr_3y",
        "Revenue CAGR (3Y)",
        "Growth",
        "percent",
        "(Revenue ÷ revenue 3 fiscal years earlier)^(1/3) − 1",
        lambda c: f.cagr(c.cur("revenue"), c.year_ago("revenue", 3), 3),
        annual_only=True,
    ),
    MetricDef(
        "revenue_cagr_5y",
        "Revenue CAGR (5Y)",
        "Growth",
        "percent",
        "(Revenue ÷ revenue 5 fiscal years earlier)^(1/5) − 1",
        lambda c: f.cagr(c.cur("revenue"), c.year_ago("revenue", 5), 5),
        annual_only=True,
    ),
    MetricDef(
        "net_income_growth_yoy",
        "Net income growth (YoY)",
        "Growth",
        "percent",
        "Net income ÷ net income a year earlier − 1 (positive base only)",
        lambda c: f.growth(c.cur("net_income"), c.year_ago("net_income")),
    ),
    MetricDef(
        "eps_growth_yoy",
        "Diluted EPS growth (YoY)",
        "Growth",
        "percent",
        "Diluted EPS ÷ diluted EPS a year earlier − 1 (positive base only)",
        lambda c: f.growth(c.cur("eps_diluted"), c.year_ago("eps_diluted")),
    ),
    MetricDef(
        "fcf_growth_yoy",
        "Free cash flow growth (YoY)",
        "Growth",
        "percent",
        "FCF ÷ FCF a year earlier − 1 (positive base only)",
        lambda c: f.growth(c.fcf(), c.fcf("year_ago")),
    ),
    # Profitability
    MetricDef(
        "gross_margin",
        "Gross margin",
        "Profitability",
        "percent",
        "Gross profit ÷ revenue",
        lambda c: f.margin(c.cur("gross_profit"), c.cur("revenue")),
    ),
    MetricDef(
        "operating_margin",
        "Operating (EBIT) margin",
        "Profitability",
        "percent",
        "Operating income ÷ revenue",
        lambda c: f.margin(c.cur("operating_income"), c.cur("revenue")),
    ),
    MetricDef(
        "ebitda_margin",
        "EBITDA margin",
        "Profitability",
        "percent",
        "(Operating income + D&A) ÷ revenue",
        lambda c: f.margin(c.ebitda(), c.cur("revenue")),
    ),
    MetricDef(
        "net_margin",
        "Net margin",
        "Profitability",
        "percent",
        "Net income ÷ revenue",
        lambda c: f.margin(c.cur("net_income"), c.cur("revenue")),
    ),
    MetricDef(
        "fcf_margin",
        "FCF margin",
        "Profitability",
        "percent",
        "(Operating cash flow − capex) ÷ revenue",
        lambda c: f.margin(c.fcf(), c.cur("revenue")),
    ),
    MetricDef(
        "ebitda",
        "EBITDA",
        "Profitability",
        "currency",
        "Operating income + depreciation & amortization",
        lambda c: c.ebitda(),
    ),
    # Returns (annual: flows over a full year against average balances)
    MetricDef(
        "roe",
        "Return on equity",
        "Returns",
        "percent",
        "Net income ÷ average shareholders' equity",
        lambda c: f.return_on(
            c.cur("net_income"),
            f.average(c.previous("total_equity"), c.cur("total_equity")),
        ),
        annual_only=True,
    ),
    MetricDef(
        "roa",
        "Return on assets",
        "Returns",
        "percent",
        "Net income ÷ average total assets",
        lambda c: f.return_on(
            c.cur("net_income"),
            f.average(c.previous("total_assets"), c.cur("total_assets")),
        ),
        annual_only=True,
    ),
    MetricDef(
        "roic",
        "Return on invested capital",
        "Returns",
        "percent",
        "Operating income × (1 − effective tax rate) ÷ average (equity + debt − cash)",
        _roic,
        annual_only=True,
    ),
    # Liquidity
    MetricDef(
        "current_ratio",
        "Current ratio",
        "Liquidity",
        "ratio",
        "Current assets ÷ current liabilities",
        lambda c: f.ratio(c.cur("current_assets"), c.cur("current_liabilities")),
    ),
    MetricDef(
        "quick_ratio",
        "Quick ratio",
        "Liquidity",
        "ratio",
        "(Cash + short-term investments + receivables) ÷ current liabilities",
        lambda c: f.ratio(
            f.quick_assets(c.cur("cash"), c.cur("short_term_investments"), c.cur("receivables")),
            c.cur("current_liabilities"),
        ),
    ),
    MetricDef(
        "working_capital",
        "Working capital",
        "Liquidity",
        "currency",
        "Current assets − current liabilities",
        lambda c: _working_capital(c.cur),
    ),
    MetricDef(
        "working_capital_change",
        "Change in working capital",
        "Liquidity",
        "currency",
        "Working capital − previous period's working capital",
        lambda c: f.difference(_working_capital(c.cur), _working_capital(c.previous)),
    ),
    MetricDef(
        "inventory_change",
        "Change in inventory",
        "Liquidity",
        "currency",
        "Inventory − previous period's inventory",
        lambda c: f.difference(c.cur("inventory"), c.previous("inventory")),
    ),
    MetricDef(
        "receivables_change",
        "Change in receivables",
        "Liquidity",
        "currency",
        "Receivables − previous period's receivables",
        lambda c: f.difference(c.cur("receivables"), c.previous("receivables")),
    ),
    # Leverage
    MetricDef(
        "total_debt",
        "Total debt",
        "Leverage",
        "currency",
        "Short-term debt + long-term debt (reported components)",
        lambda c: c.debt(),
    ),
    MetricDef(
        "debt_to_equity",
        "Debt / equity",
        "Leverage",
        "ratio",
        "Total debt ÷ shareholders' equity (positive equity only)",
        lambda c: f.return_on(c.debt(), c.cur("total_equity")),
    ),
    MetricDef(
        "debt_to_ebitda",
        "Debt / EBITDA",
        "Leverage",
        "ratio",
        "Total debt ÷ EBITDA (full fiscal year)",
        lambda c: f.leverage_to_ebitda(c.debt(), c.ebitda()),
        annual_only=True,
    ),
    MetricDef(
        "net_debt_to_ebitda",
        "Net debt / EBITDA",
        "Leverage",
        "ratio",
        "(Total debt − cash) ÷ EBITDA (full fiscal year)",
        lambda c: f.leverage_to_ebitda(f.difference(c.debt(), c.cur("cash")), c.ebitda()),
        annual_only=True,
    ),
    MetricDef(
        "interest_coverage",
        "Interest coverage",
        "Leverage",
        "ratio",
        "Operating income ÷ interest expense",
        lambda c: f.interest_coverage(c.cur("operating_income"), c.cur("interest_expense")),
    ),
    # Cash flow
    MetricDef(
        "operating_cash_flow",
        "Operating cash flow",
        "Cash flow",
        "currency",
        "Reported net cash from operating activities",
        lambda c: c.cur("operating_cash_flow"),
    ),
    MetricDef(
        "capex",
        "Capital expenditures",
        "Cash flow",
        "currency",
        "Reported payments for property, plant & equipment",
        lambda c: c.cur("capex"),
    ),
    MetricDef(
        "free_cash_flow",
        "Free cash flow",
        "Cash flow",
        "currency",
        "Operating cash flow − capital expenditures",
        lambda c: c.fcf(),
    ),
    MetricDef(
        "capex_to_revenue",
        "Capex / revenue",
        "Cash flow",
        "percent",
        "Capital expenditures ÷ revenue",
        lambda c: f.margin(c.cur("capex"), c.cur("revenue")),
    ),
    # Shareholders
    MetricDef(
        "share_dilution_yoy",
        "Share count change (YoY)",
        "Shareholders",
        "percent",
        "Weighted diluted shares ÷ a year earlier − 1",
        lambda c: f.growth(c.cur("shares_diluted"), c.year_ago("shares_diluted")),
    ),
    MetricDef(
        "sbc_to_revenue",
        "Stock-based comp / revenue",
        "Shareholders",
        "percent",
        "Stock-based compensation ÷ revenue",
        lambda c: f.margin(c.cur("stock_based_compensation"), c.cur("revenue")),
    ),
    MetricDef(
        "dividend_growth_yoy",
        "Dividend per share growth (YoY)",
        "Shareholders",
        "percent",
        "Dividends declared per share ÷ a year earlier − 1",
        lambda c: f.growth(c.cur("dividends_per_share"), c.year_ago("dividends_per_share")),
    ),
    MetricDef(
        "payout_ratio",
        "Dividend payout ratio",
        "Shareholders",
        "percent",
        "Dividends paid ÷ net income (positive net income only)",
        lambda c: f.return_on(c.cur("dividends_paid"), c.cur("net_income")),
    ),
    MetricDef(
        "shareholder_returns",
        "Dividends + buybacks",
        "Shareholders",
        "currency",
        "Dividends paid + share repurchases",
        lambda c: f.total(c.cur("dividends_paid"), c.cur("buybacks")),
    ),
)

METRICS_BY_KEY = {metric.key: metric for metric in METRICS}


def metric_series(statements: StatementSet) -> dict[str, dict[str, Decimal]]:
    """metric key -> period key -> value, for every metric defined for the period type."""

    series: dict[str, dict[str, Decimal]] = {}
    annual = statements.period_type == "annual"
    for index, period in enumerate(statements.periods):
        context = Context(statements, index)
        for metric in METRICS:
            if metric.annual_only and not annual:
                continue
            value = metric.compute(context)
            if value is not None:
                series.setdefault(metric.key, {})[period.key] = value
    return series
