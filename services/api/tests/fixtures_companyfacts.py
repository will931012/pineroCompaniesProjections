"""Synthetic SEC companyfacts payload for a fictional issuer (test fixture only).

Shape follows https://data.sec.gov/api/xbrl/companyfacts/. Numbers are small and chosen so
expected results can be verified by hand:

FY2023: 2022-10-01 → 2023-09-30        FY2024: 2023-10-01 → 2024-09-28 (52-week year)
FY2024 quarters end 2023-12-30, 2024-03-30, 2024-06-29, 2024-09-28.

- Revenue FY2023 first reported 1000 (10-K 2023-11-03), restated to 990 (10-K/A 2024-01-15)
  and repeated as a comparative in the FY2024 10-K.
- Revenue FY2024 1200; quarters 280, 300, 290 reported; Q4 must be derived: 1200 − 870 = 330.
- Operating cash flow is YTD only: 60, 130, 210, 300 → quarters 60, 70, 80, 90.
- Two share classes on the cover page: 40 + 20 = 60 shares.
"""

from typing import Any

CIK = 999001
FY23 = ("2022-10-01", "2023-09-30")
FY24 = ("2023-10-01", "2024-09-28")
Q1, Q2, Q3 = "2023-12-30", "2024-03-30", "2024-06-29"

K23 = {
    "accn": "0000999001-23-000010",
    "form": "10-K",
    "filed": "2023-11-03",
    "fy": 2023,
    "fp": "FY",
}
K23A = {
    "accn": "0000999001-24-000002",
    "form": "10-K/A",
    "filed": "2024-01-15",
    "fy": 2023,
    "fp": "FY",
}
Q1F = {
    "accn": "0000999001-24-000005",
    "form": "10-Q",
    "filed": "2024-02-02",
    "fy": 2024,
    "fp": "Q1",
}
Q2F = {
    "accn": "0000999001-24-000020",
    "form": "10-Q",
    "filed": "2024-05-03",
    "fy": 2024,
    "fp": "Q2",
}
Q3F = {
    "accn": "0000999001-24-000030",
    "form": "10-Q",
    "filed": "2024-08-02",
    "fy": 2024,
    "fp": "Q3",
}
K24 = {
    "accn": "0000999001-24-000040",
    "form": "10-K",
    "filed": "2024-11-01",
    "fy": 2024,
    "fp": "FY",
}


def dur(start: str, end: str, val: float, filing: dict[str, Any]) -> dict[str, Any]:
    return {"start": start, "end": end, "val": val, **filing}


def inst(end: str, val: float, filing: dict[str, Any]) -> dict[str, Any]:
    return {"end": end, "val": val, **filing}


def usd(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"units": {"USD": list(entries)}}


def companyfacts(scale: float = 1.0) -> dict[str, Any]:
    """`scale` multiplies every USD amount, to create comparable peers."""

    def s(value: float) -> float:
        return value * scale

    gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": usd(
            dur(*FY23, s(1000), K23),
            dur(*FY23, s(990), K23A),
            dur(*FY23, s(990), K24),
            dur(FY24[0], Q1, s(280), Q1F),
            dur("2023-12-31", Q2, s(300), Q2F),
            dur(FY24[0], Q2, s(580), Q2F),
            dur("2024-03-31", Q3, s(290), Q3F),
            dur(FY24[0], Q3, s(870), Q3F),
            dur(*FY24, s(1200), K24),
            # An 8-K fact is outside the accepted forms and must be ignored.
            dur(*FY24, s(9999), {**K24, "form": "8-K", "accn": "0000999001-24-000099"}),
        ),
        "OperatingIncomeLoss": usd(dur(*FY23, s(150), K23), dur(*FY24, s(200), K24)),
        "NetIncomeLoss": usd(dur(*FY23, s(100), K23), dur(*FY24, s(150), K24)),
        "IncomeTaxExpenseBenefit": usd(dur(*FY24, s(36), K24)),
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
            "ExtraordinaryItemsNoncontrollingInterest"
        ): usd(dur(*FY24, s(180), K24)),
        "DepreciationDepletionAndAmortization": usd(dur(*FY24, s(30), K24)),
        "NetCashProvidedByUsedInOperatingActivities": usd(
            dur(*FY23, s(200), K23),
            dur(FY24[0], Q1, s(60), Q1F),
            dur(FY24[0], Q2, s(130), Q2F),
            dur(FY24[0], Q3, s(210), Q3F),
            dur(*FY24, s(300), K24),
        ),
        "PaymentsToAcquirePropertyPlantAndEquipment": usd(
            dur(*FY23, s(40), K23), dur(*FY24, s(50), K24)
        ),
        "Assets": usd(inst(FY23[1], s(2000), K23), inst(FY24[1], s(2400), K24)),
        "StockholdersEquity": usd(inst(FY23[1], s(800), K23), inst(FY24[1], s(1000), K24)),
        "LongTermDebtNoncurrent": usd(inst(FY23[1], s(400), K23), inst(FY24[1], s(400), K24)),
        "DebtCurrent": usd(inst(FY23[1], s(100), K23), inst(FY24[1], s(100), K24)),
        "CashAndCashEquivalentsAtCarryingValue": usd(
            inst(FY23[1], s(300), K23),
            inst(Q1, s(310), Q1F),
            inst(Q2, s(320), Q2F),
            inst(Q3, s(330), Q3F),
            inst(FY24[1], s(350), K24),
            # Malformed value: rejected and counted.
            inst(FY24[1], "n/a", {**K24, "accn": "0000999001-24-000098"}),  # type: ignore[arg-type]
        ),
        "AssetsCurrent": usd(inst(FY24[1], s(900), K24)),
        "LiabilitiesCurrent": usd(inst(FY24[1], s(600), K24)),
        "EarningsPerShareDiluted": {
            "units": {
                "USD/shares": [
                    dur(*FY23, 2.00, K23),
                    dur(*FY24, 2.50, K24),
                ]
            }
        },
    }
    dei = {
        "EntityCommonStockSharesOutstanding": {
            "units": {
                "shares": [
                    inst("2024-10-18", 40, K24),
                    inst("2024-10-18", 20, K24),
                    inst("2023-10-20", 58, K23),
                ]
            }
        },
    }
    return {
        "cik": CIK,
        "entityName": "Example Devices Inc.",
        "facts": {"us-gaap": gaap, "dei": dei},
    }
