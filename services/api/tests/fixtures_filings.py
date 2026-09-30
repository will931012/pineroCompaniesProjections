"""Synthetic EDGAR filings for a fictional issuer (test fixtures only).

Shapes follow data.sec.gov submissions and www.sec.gov/Archives documents. The 10-Ks carry
a table of contents, hidden inline-XBRL headers, scripts, page numbers, and "Table of
Contents" links, like real filings. Risk factors change between the two 10-Ks:
paragraph A is unchanged, B is edited, C is removed, and D is new.
"""

from typing import Any

CIK = 999001
OTHER_ISSUER_CIK = 555001

RISK_A = "Our business depends on a small number of contract manufacturers located in Asia."
RISK_B_2023 = "Currency fluctuations may reduce our reported revenue and gross margin."
RISK_B_2024 = (
    "Currency fluctuations may materially reduce our reported revenue, gross margin, and cash flow."
)
RISK_C = "We face intense competition from larger companies with greater resources."
RISK_D = "Export controls on advanced semiconductors could restrict sales to certain customers."


def _ten_k(fiscal_year: int, risks: list[str], business: str) -> bytes:
    toc = "".join(
        f"<tr><td>Item {item}.</td><td>{title}</td><td>{page}</td></tr>"
        for item, title, page in [
            ("1", "Business", 3),
            ("1A", "Risk Factors", 12),
            ("7", "Management's Discussion and Analysis", 30),
            ("8", "Financial Statements and Supplementary Data", 45),
        ]
    )
    risk_html = "".join(f"<p>{r}</p>" for r in risks)
    return f"""<html><head><title>10-K</title><style>p {{ margin: 0 }}</style></head><body>
<div style="display:none"><ix:header><ix:hidden>dei:EntityCentralIndexKey 0000999001</ix:hidden></ix:header></div>
<script>window.tracking = true;</script>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p>FORM 10-K</p><p>Fiscal year {fiscal_year}</p>
<table>{toc}</table>
<p>PART I</p>
<div><span style="font-weight:bold">Item 1.</span></div><div>Business</div>
<p>{business}</p>
<p>2</p><p><a href="#toc">Table of Contents</a></p>
<div><b>Item 1A. Risk Factors</b></div>
{risk_html}
<p>12</p>
<p>PART II</p>
<div><b>Item 7. Management&#8217;s Discussion and Analysis</b></div>
<p>Net sales increased&#160;8% in fiscal {fiscal_year}, driven by services. See Item 1A for risks.</p>
<table><tr><td>Net sales</td><td>$</td><td>1,200</td></tr><tr><td>Cost of sales</td><td></td><td>700</td></tr></table>
<div><b>Item 8. Financial Statements and Supplementary Data</b></div>
<p>The consolidated financial statements are included below.</p>
<p>SIGNATURES</p><p>Pursuant to the requirements of Section 13, the registrant has duly caused this report to be signed.</p>
</body></html>""".encode()


TEN_K_2024 = _ten_k(
    2024, [RISK_A, RISK_B_2024, RISK_D], "We design and sell connected devices and services."
)
TEN_K_2023 = _ten_k(
    2023, [RISK_A, RISK_B_2023, RISK_C], "We design and sell connected devices and services."
)

TEN_Q = b"""<html><body>
<p>PART I - FINANCIAL INFORMATION</p>
<p>Item 1. Financial Statements</p><p>Condensed consolidated statements of operations follow.</p>
<p>Item 2. Management's Discussion and Analysis of Financial Condition</p>
<p>Revenue grew 5% in the quarter as supply constraints eased.</p>
<p>PART II - OTHER INFORMATION</p>
<p>Item 1. Legal Proceedings</p><p>There are no material pending legal proceedings.</p>
<p>Item 1A. Risk Factors</p><p>There have been no material changes to our risk factors.</p>
</body></html>"""

EIGHT_K = b"""<html><body>
<p>FORM 8-K</p>
<p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>On October 30, 2024, the Company announced financial results for its fourth fiscal quarter.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<p>Exhibit 99.1 Press release dated October 30, 2024.</p>
<p>SIGNATURES</p><p>Jane Doe, Chief Financial Officer</p>
</body></html>"""


def form4_xml(issuer_cik: int = CIK) -> bytes:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2024-11-05</periodOfReport>
  <issuer><issuerCik>{issuer_cik:010d}</issuerCik><issuerName>Example Devices Inc.</issuerName><issuerTradingSymbol>EXDV</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0001111111</rptOwnerCik><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther><officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>1</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2024-11-04</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>S</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>150.25</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>52000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2024-11-05</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts><transactionShares><value>200</value></transactionShares><transactionPricePerShare><value>140</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>52200</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>I</value></directOrIndirectOwnership><natureOfOwnership><value>By Trust</value></natureOfOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Stock Option (right to buy)</value></securityTitle>
      <conversionOrExercisePrice><value>35.5</value></conversionOrExercisePrice>
      <transactionDate><value>2024-11-04</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>M</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <underlyingSecurity><underlyingSecurityTitle><value>Common Stock</value></underlyingSecurityTitle><underlyingSecurityShares><value>1000</value></underlyingSecurityShares></underlyingSecurity>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>0</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </derivativeTransaction>
  </derivativeTable>
  <footnotes><footnote id="F1">Price not applicable to option exercise.</footnote></footnotes>
</ownershipDocument>""".encode()


XXE_FORM4 = b"""<?xml version="1.0"?>
<!DOCTYPE ownershipDocument [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<ownershipDocument><issuer><issuerCik>0000999001</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>&secret;</rptOwnerName></reportingOwnerId></reportingOwner>
</ownershipDocument>"""

# (accession, form, filed, report date, primary document, description, 8-K items)
FILINGS: list[tuple[str, str, str, str, str, str, str]] = [
    ("0000999001-24-000050", "4", "2024-11-06", "2024-11-05", "xslF345X05/wk-form4_1.xml", "", ""),
    ("0000999001-24-000049", "4", "2024-11-06", "2024-11-05", "xslF345X05/other.xml", "", ""),
    ("0000999001-24-000040", "10-K", "2024-11-01", "2024-09-28", "exdv-20240928.htm", "10-K", ""),
    ("0000999001-24-000035", "8-K", "2024-10-30", "2024-10-30", "exdv-8k.htm", "8-K", "2.02,9.01"),
    ("0000999001-24-000030", "10-Q", "2024-08-02", "2024-06-29", "exdv-20240629.htm", "10-Q", ""),
    ("0000999001-23-000010", "10-K", "2023-11-03", "2023-09-30", "exdv-20230930.htm", "10-K", ""),
]
OLD_FILING = ("0000999001-09-000001", "10-K", "2009-10-27", "2009-09-26", "d10k.htm", "10-K", "")


def columns(rows: list[tuple[str, str, str, str, str, str, str]]) -> dict[str, list[Any]]:
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "acceptanceDateTime": [f"{r[2]}T16:30:12.000Z" for r in rows],
        "primaryDocument": [r[4] for r in rows],
        "primaryDocDescription": [r[5] for r in rows],
        "items": [r[6] for r in rows],
        "size": [123_456 for _ in rows],
        "isXBRL": [1 if r[1] in {"10-K", "10-Q"} else 0 for r in rows],
        "isInlineXBRL": [1 if r[1] in {"10-K", "10-Q"} else 0 for r in rows],
    }


def filings_block() -> dict[str, Any]:
    return {
        "recent": columns(FILINGS),
        "files": [{"name": f"CIK{CIK:010d}-submissions-001.json", "filingCount": 1}],
    }


def archive(accession: str, document: str) -> str:
    return f"/Archives/edgar/data/{CIK}/{accession.replace('-', '')}/{document}"


DOCUMENTS: dict[str, bytes] = {
    archive("0000999001-24-000040", "exdv-20240928.htm"): TEN_K_2024,
    archive("0000999001-23-000010", "exdv-20230930.htm"): TEN_K_2023,
    archive("0000999001-24-000030", "exdv-20240629.htm"): TEN_Q,
    archive("0000999001-24-000035", "exdv-8k.htm"): EIGHT_K,
    archive("0000999001-24-000050", "wk-form4_1.xml"): form4_xml(),
    archive("0000999001-24-000049", "other.xml"): form4_xml(OTHER_ISSUER_CIK),
}
PAGES = {f"CIK{CIK:010d}-submissions-001.json": columns([OLD_FILING])}
