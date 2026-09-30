"""Canonical financial line items and the XBRL concepts that can supply them.

Candidates are tried in order for each period; the first concept with a value wins and
is recorded next to the value, so every number shows which filing tag produced it.
Bump MAPPING_VERSION whenever candidates or semantics change.
"""

from dataclasses import dataclass
from typing import Literal

MAPPING_VERSION = "2026.09-2"

Statement = Literal["income", "balance", "cash_flow"]
# flow: additive over time (quarters sum to the year); instant: point-in-time balance;
# nonadditive: per-share or average values that cannot be differenced across periods.
Kind = Literal["flow", "instant", "nonadditive"]
Unit = Literal["USD", "USD/shares", "shares"]


@dataclass(frozen=True)
class LineItem:
    key: str
    label: str
    statement: Statement
    kind: Kind
    unit: Unit
    concepts: tuple[str, ...]
    taxonomy: str = "us-gaap"


LINE_ITEMS: tuple[LineItem, ...] = (
    # Income statement
    LineItem(
        "revenue",
        "Revenue",
        "income",
        "flow",
        "USD",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "RevenuesNetOfInterestExpense",
        ),
    ),
    LineItem(
        "cost_of_revenue",
        "Cost of revenue",
        "income",
        "flow",
        "USD",
        (
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfServices",
        ),
    ),
    LineItem("gross_profit", "Gross profit", "income", "flow", "USD", ("GrossProfit",)),
    LineItem(
        "research_development",
        "Research & development",
        "income",
        "flow",
        "USD",
        ("ResearchAndDevelopmentExpense",),
    ),
    LineItem(
        "sga",
        "Selling, general & administrative",
        "income",
        "flow",
        "USD",
        ("SellingGeneralAndAdministrativeExpense",),
    ),
    LineItem(
        "operating_income",
        "Operating income",
        "income",
        "flow",
        "USD",
        ("OperatingIncomeLoss",),
    ),
    LineItem(
        "interest_expense",
        "Interest expense",
        "income",
        "flow",
        "USD",
        (
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestExpenseDebt",
        ),
    ),
    LineItem(
        "pretax_income",
        "Pre-tax income",
        "income",
        "flow",
        "USD",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    LineItem(
        "income_tax",
        "Income tax expense",
        "income",
        "flow",
        "USD",
        ("IncomeTaxExpenseBenefit",),
    ),
    LineItem(
        "net_income",
        "Net income",
        "income",
        "flow",
        "USD",
        (
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
    ),
    LineItem(
        "eps_basic",
        "EPS (basic)",
        "income",
        "nonadditive",
        "USD/shares",
        ("EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"),
    ),
    LineItem(
        "eps_diluted",
        "EPS (diluted)",
        "income",
        "nonadditive",
        "USD/shares",
        ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    ),
    LineItem(
        "shares_basic",
        "Weighted basic shares",
        "income",
        "nonadditive",
        "shares",
        ("WeightedAverageNumberOfSharesOutstandingBasic",),
    ),
    LineItem(
        "shares_diluted",
        "Weighted diluted shares",
        "income",
        "nonadditive",
        "shares",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
    # Balance sheet
    LineItem(
        "cash",
        "Cash & equivalents",
        "balance",
        "instant",
        "USD",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    ),
    LineItem(
        "short_term_investments",
        "Short-term investments",
        "balance",
        "instant",
        "USD",
        (
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        ),
    ),
    LineItem(
        "receivables",
        "Receivables",
        "balance",
        "instant",
        "USD",
        (
            "AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
        ),
    ),
    LineItem("inventory", "Inventory", "balance", "instant", "USD", ("InventoryNet",)),
    LineItem(
        "current_assets",
        "Total current assets",
        "balance",
        "instant",
        "USD",
        ("AssetsCurrent",),
    ),
    LineItem("total_assets", "Total assets", "balance", "instant", "USD", ("Assets",)),
    LineItem(
        "accounts_payable",
        "Accounts payable",
        "balance",
        "instant",
        "USD",
        ("AccountsPayableCurrent",),
    ),
    LineItem(
        "current_liabilities",
        "Total current liabilities",
        "balance",
        "instant",
        "USD",
        ("LiabilitiesCurrent",),
    ),
    LineItem(
        "short_term_debt",
        "Short-term debt",
        "balance",
        "instant",
        "USD",
        (
            "DebtCurrent",
            "LongTermDebtCurrent",
            "ShortTermBorrowings",
        ),
    ),
    LineItem(
        "long_term_debt",
        "Long-term debt",
        "balance",
        "instant",
        "USD",
        (
            "LongTermDebtNoncurrent",
            "LongTermDebtAndCapitalLeaseObligations",
        ),
    ),
    LineItem(
        "total_liabilities",
        "Total liabilities",
        "balance",
        "instant",
        "USD",
        ("Liabilities",),
    ),
    LineItem(
        "total_equity",
        "Shareholders' equity",
        "balance",
        "instant",
        "USD",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    LineItem(
        "common_shares_outstanding",
        "Common shares outstanding",
        "balance",
        "instant",
        "shares",
        ("CommonStockSharesOutstanding",),
    ),
    # Cash flow statement
    LineItem(
        "operating_cash_flow",
        "Operating cash flow",
        "cash_flow",
        "flow",
        "USD",
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    LineItem(
        "capex",
        "Capital expenditures",
        "cash_flow",
        "flow",
        "USD",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
    ),
    LineItem(
        "depreciation_amortization",
        "Depreciation & amortization",
        "cash_flow",
        "flow",
        "USD",
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
        ),
    ),
    LineItem(
        "stock_based_compensation",
        "Stock-based compensation",
        "cash_flow",
        "flow",
        "USD",
        ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    ),
    LineItem(
        "dividends_paid",
        "Dividends paid",
        "cash_flow",
        "flow",
        "USD",
        (
            "PaymentsOfDividends",
            "PaymentsOfDividendsCommonStock",
        ),
    ),
    LineItem(
        "buybacks",
        "Share repurchases",
        "cash_flow",
        "flow",
        "USD",
        ("PaymentsForRepurchaseOfCommonStock",),
    ),
    LineItem(
        "dividends_per_share",
        "Dividends per share (declared)",
        "cash_flow",
        "flow",
        "USD/shares",
        (
            "CommonStockDividendsPerShareDeclared",
            "CommonStockDividendsPerShareCashPaid",
        ),
    ),
    LineItem(
        "investing_cash_flow",
        "Investing cash flow",
        "cash_flow",
        "flow",
        "USD",
        ("NetCashProvidedByUsedInInvestingActivities",),
    ),
    LineItem(
        "financing_cash_flow",
        "Financing cash flow",
        "cash_flow",
        "flow",
        "USD",
        ("NetCashProvidedByUsedInFinancingActivities",),
    ),
)

# Cover-page share count (dei), used for market capitalisation.
SHARES_OUTSTANDING = LineItem(
    "shares_outstanding",
    "Shares outstanding (cover page)",
    "balance",
    "instant",
    "shares",
    ("EntityCommonStockSharesOutstanding",),
    taxonomy="dei",
)

LINE_ITEMS_BY_KEY = {item.key: item for item in (*LINE_ITEMS, SHARES_OUTSTANDING)}

# (taxonomy, concept) pairs that ingestion keeps; everything else in companyfacts is ignored.
TRACKED_CONCEPTS: frozenset[tuple[str, str]] = frozenset(
    (item.taxonomy, concept)
    for item in (*LINE_ITEMS, SHARES_OUTSTANDING)
    for concept in item.concepts
)

# Periodic reports whose facts are used. Registration statements and 8-K exhibits are not.
ACCEPTED_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A"})
