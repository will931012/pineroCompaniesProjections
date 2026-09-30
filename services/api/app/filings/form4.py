"""Parse SEC Form 4 ownership XML into transaction rows.

The XML is untrusted input: entity resolution and network access are disabled. Values are
taken as reported; a field SEC left empty (for example a price given only in a footnote)
stays empty rather than being inferred.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree

FORM4_PARSER_VERSION = "form4-2026.09-1"


def _parser() -> etree.XMLParser:
    # A new parser per document: lxml parsers must not be shared across threads.
    return etree.XMLParser(
        resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False
    )


class Form4Error(ValueError):
    pass


@dataclass(frozen=True)
class Owner:
    cik: int | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str | None


@dataclass(frozen=True)
class ParsedTransaction:
    is_derivative: bool
    ordinal: int
    security_title: str | None
    transaction_date: date | None
    transaction_code: str | None
    shares: Decimal | None
    price: Decimal | None
    acquired_disposed: str | None
    shares_owned_after: Decimal | None
    ownership: str | None
    ownership_nature: str | None
    underlying_security: str | None
    exercise_price: Decimal | None


@dataclass(frozen=True)
class ParsedForm4:
    issuer_cik: int | None
    owners: list[Owner]
    rule_10b5_1: bool | None
    transactions: list[ParsedTransaction]


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(element: etree._Element | None, *path: str) -> etree._Element | None:
    for name in path:
        if element is None:
            return None
        element = next((c for c in element if _local(c.tag) == name), None)
    return element


def _text(element: etree._Element | None, *path: str) -> str | None:
    found = _child(element, *path)
    if found is None:
        return None
    # Most fields wrap their content in <value>; some (names, CIKs) do not.
    value = _child(found, "value")
    raw = (value if value is not None else found).text
    raw = raw.strip() if raw else None
    return raw or None


def _flag(element: etree._Element | None, *path: str) -> bool:
    return (_text(element, *path) or "").lower() in {"1", "true"}


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _clip(value: str | None, length: int) -> str | None:
    return value[:length] if value else None


def _transaction(node: etree._Element, derivative: bool, ordinal: int) -> ParsedTransaction:
    code = _text(node, "transactionCoding", "transactionCode")
    return ParsedTransaction(
        is_derivative=derivative,
        ordinal=ordinal,
        security_title=_clip(_text(node, "securityTitle"), 200),
        transaction_date=_date(_text(node, "transactionDate")),
        transaction_code=code[:4] if code else None,
        shares=_decimal(_text(node, "transactionAmounts", "transactionShares")),
        price=_decimal(_text(node, "transactionAmounts", "transactionPricePerShare")),
        acquired_disposed=_clip(
            _text(node, "transactionAmounts", "transactionAcquiredDisposedCode"), 1
        ),
        shares_owned_after=_decimal(
            _text(node, "postTransactionAmounts", "sharesOwnedFollowingTransaction")
        ),
        ownership=_clip(_text(node, "ownershipNature", "directOrIndirectOwnership"), 1),
        ownership_nature=_clip(_text(node, "ownershipNature", "natureOfOwnership"), 200),
        underlying_security=_clip(_text(node, "underlyingSecurity", "underlyingSecurityTitle"), 200)
        if derivative
        else None,
        exercise_price=_decimal(_text(node, "conversionOrExercisePrice")) if derivative else None,
    )


def parse_form4(content: bytes) -> ParsedForm4:
    try:
        root = etree.fromstring(content, _parser())
    except etree.XMLSyntaxError as exc:
        raise Form4Error(f"Invalid ownership XML: {exc}") from exc
    if _local(root.tag) != "ownershipDocument":
        raise Form4Error("Not an SEC ownership document.")

    owners = []
    for node in (c for c in root if _local(c.tag) == "reportingOwner"):
        relationship = _child(node, "reportingOwnerRelationship")
        owners.append(
            Owner(
                cik=_int(_text(node, "reportingOwnerId", "rptOwnerCik")),
                name=(_text(node, "reportingOwnerId", "rptOwnerName") or "Unknown")[:200],
                is_director=_flag(relationship, "isDirector"),
                is_officer=_flag(relationship, "isOfficer"),
                is_ten_percent_owner=_flag(relationship, "isTenPercentOwner"),
                is_other=_flag(relationship, "isOther"),
                officer_title=_clip(_text(relationship, "officerTitle"), 200),
            )
        )
    transactions: list[ParsedTransaction] = []
    for table, row, derivative in (
        ("nonDerivativeTable", "nonDerivativeTransaction", False),
        ("derivativeTable", "derivativeTransaction", True),
    ):
        container = _child(root, table)
        rows = [c for c in container if _local(c.tag) == row] if container is not None else []
        transactions.extend(_transaction(node, derivative, i) for i, node in enumerate(rows))
    ten_b5 = _text(root, "aff10b5One")
    return ParsedForm4(
        issuer_cik=_int(_text(root, "issuer", "issuerCik")),
        owners=owners,
        rule_10b5_1=None if ten_b5 is None else ten_b5.lower() in {"1", "true"},
        transactions=transactions,
    )


def raw_xml_name(primary_document: str) -> str:
    """EDGAR lists Form 4s by their rendered view (xslF345X05/form4.xml); the XML is the
    same file name at the folder root."""
    return primary_document.rsplit("/", 1)[-1]
