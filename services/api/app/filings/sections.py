"""Split a filing's paragraphs into its items (10-K, 10-Q, 8-K).

Item headings appear twice in most reports: in the table of contents and at the start of
the item. For each item the heading that starts the longest run of text is used, which
skips table-of-contents entries and short cross-references without positional rules. A
document with no recognisable items becomes a single "document" section. Section text is
the document's own paragraphs, unchanged, separated by blank lines.
"""

import re
from dataclasses import dataclass

# 2026.09-4: earnings 8-Ks include their EX-99 press release as a section.
# 2026.09-5: EDGAR labels at the top of exhibits are dropped.
EXTRACTOR_VERSION = "2026.09-5"

TEN_K_ITEMS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity and Related Stockholder Matters",
    "6": "[Reserved]",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}
TEN_Q_ITEMS: dict[tuple[int, str], str] = {
    (1, "1"): "Financial Statements",
    (1, "2"): "Management's Discussion and Analysis",
    (1, "3"): "Quantitative and Qualitative Disclosures About Market Risk",
    (1, "4"): "Controls and Procedures",
    (2, "1"): "Legal Proceedings",
    (2, "1A"): "Risk Factors",
    (2, "2"): "Unregistered Sales of Equity Securities and Use of Proceeds",
    (2, "3"): "Defaults Upon Senior Securities",
    (2, "4"): "Mine Safety Disclosures",
    (2, "5"): "Other Information",
    (2, "6"): "Exhibits",
}
EIGHT_K_ITEMS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety – Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate or Increase a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Appointment of Directors or Certain Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "5.05": "Amendments to the Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

_MAX_HEADING = 220
_ITEM = re.compile(r"^items?\s*(\d{1,2}[a-d]?)\b\s*[.:\-–—)]?\s*(.*)$", re.IGNORECASE)
_EIGHT_K_ITEM = re.compile(r"^item\s*(\d{1,2}\.\d{2})\b\s*[.:\-–—]?\s*(.*)$", re.IGNORECASE)
_PART = re.compile(r"^part\s+(iv|i{1,3})\b", re.IGNORECASE)
_SIGNATURES = re.compile(r"^signatures?$", re.IGNORECASE)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


@dataclass(frozen=True)
class Section:
    key: str
    part: str | None
    item: str | None
    title: str
    ordinal: int
    text: str


@dataclass(frozen=True)
class _Heading:
    index: int
    key: str
    part: str | None
    item: str
    title: str
    # Text after "Item 1A." on the heading line; empty when the title is on the next line.
    rest: str = ""


def _heading_text(paragraph: str) -> str:
    return paragraph.replace("|", " ").replace("’", "'").strip().rstrip(".").strip()


def form_family(form: str) -> str:
    base = form.upper().removesuffix("/A")
    if base in {"10-K", "10-KT", "10-K405"}:
        return "10-K"
    if base in {"10-Q", "10-QT"}:
        return "10-Q"
    if base == "8-K":
        return "8-K"
    return base


def _candidates(paragraphs: list[str], family: str) -> list[_Heading]:
    found: list[_Heading] = []
    part: int | None = None
    for index, paragraph in enumerate(paragraphs):
        if len(paragraph) > _MAX_HEADING:
            continue
        heading = _heading_text(paragraph)
        part_match = _PART.match(heading)
        if part_match and family in {"10-K", "10-Q"}:
            part = _ROMAN[part_match.group(1).lower()]
            # "PART I — Item 1. Financial Statements" on one line: keep looking for the item.
            heading = heading[part_match.end() :].strip(" .:-–—,")
            if not heading:
                continue
        if family == "8-K":
            match = _EIGHT_K_ITEM.match(heading)
            if match:
                item = match.group(1)
                found.append(
                    _Heading(index, f"item_{item}", None, item,
                             EIGHT_K_ITEMS.get(item, match.group(2)[:200] or f"Item {item}"))
                )  # fmt: skip
            continue
        match = _ITEM.match(heading)
        if not match:
            continue
        item = match.group(1).upper()
        rest = match.group(2).strip()
        if family == "10-K":
            if item not in TEN_K_ITEMS:
                continue
            found.append(
                _Heading(index, f"item_{item.lower()}", None, item, TEN_K_ITEMS[item], rest)
            )
        else:
            q_part = part or (2 if item in {"1A", "5", "6"} else 1)
            title = TEN_Q_ITEMS.get((q_part, item))
            if title is None:
                continue
            found.append(
                _Heading(index, f"part{q_part}_item_{item.lower()}", f"Part {'I' * q_part}", item,
                         title, rest)
            )  # fmt: skip
    return found


def _is_title_line(paragraph: str, title: str) -> bool:
    line = _heading_text(paragraph).lower()
    return 0 < len(line) <= 120 and (
        title.lower().startswith(line) or line.startswith(title.lower())
    )


def _rank(heading: _Heading, family: str) -> int:
    """Position of the item in the form's official order."""
    if family == "10-K":
        return list(TEN_K_ITEMS).index(heading.item)
    if family == "10-Q":
        part = 2 if heading.key.startswith("part2") else 1
        return list(TEN_Q_ITEMS).index((part, heading.item))
    major, minor = heading.item.split(".")
    return int(major) * 100 + int(minor)


def _increasing(values: list[int]) -> set[int]:
    """Positions of a longest strictly increasing subsequence of `values`."""
    length = [1] * len(values)
    previous = [-1] * len(values)
    for i in range(len(values)):
        for j in range(i):
            if values[j] < values[i] and length[j] + 1 > length[i]:
                length[i], previous[i] = length[j] + 1, j
    end = max(range(len(values)), key=lambda i: length[i], default=-1)
    kept: set[int] = set()
    while end != -1:
        kept.add(end)
        end = previous[end]
    return kept


def _choose(headings: list[_Heading], span: dict[int, int], family: str) -> list[_Heading]:
    """One heading per item, in the form's order.

    For each item the heading starting the longest run of text wins (this skips the table of
    contents). Items then must appear in official order: the largest in-order subset is kept,
    and each remaining item is re-chosen between its kept neighbours, or dropped.
    """
    positions: dict[str, list[int]] = {}
    for position, heading in enumerate(headings):
        positions.setdefault(heading.key, []).append(position)
    keys = sorted(positions, key=lambda k: _rank(headings[positions[k][0]], family))
    best = {k: max(positions[k], key=lambda p: (span[p], -p)) for k in keys}
    in_order = _increasing([headings[best[k]].index for k in keys])
    chosen: dict[str, int] = {keys[i]: best[keys[i]] for i in in_order}
    for number, key in enumerate(keys):
        if key in chosen:
            continue
        lower = max((headings[chosen[k]].index for k in keys[:number] if k in chosen), default=-1)
        upper = min(
            (headings[chosen[k]].index for k in keys[number + 1 :] if k in chosen),
            default=10**12,
        )
        window = [p for p in positions[key] if lower < headings[p].index < upper]
        if window:
            chosen[key] = max(window, key=lambda p: (span[p], -p))
    return sorted((headings[p] for p in chosen.values()), key=lambda h: h.index)


# Items that normally hold little text; one of them holding most of the document means the
# report is not organised under item headings (e.g. JPMorgan's 10-Q, which uses a
# cross-reference index), so labelling the text with that item would be wrong.
_TRAILING_ITEMS = {"item_15", "item_16", "part2_item_6", "item_9.01"}
# Every 10-K and 10-Q has substantive MD&A; almost none of it means the items were not found.
_MDNA = {"10-K": "item_7", "10-Q": "part1_item_2"}


def extract_sections(paragraphs: list[str], form: str, fallback_title: str) -> list[Section]:
    family = form_family(form)
    found = _candidates(paragraphs, family) if family in {"10-K", "10-Q", "8-K"} else []
    # Many filings repeat "PART II / Item 7" as a running header on every page. Consecutive
    # headings of the same item are one section starting at the first; the repeats are
    # removed from the text.
    repeated: dict[str, set[int]] = {}
    for heading in found:
        repeated.setdefault(heading.key, set()).add(heading.index)
    headings = [h for i, h in enumerate(found) if i == 0 or h.key != found[i - 1].key]
    lengths = [len(p) for p in paragraphs]
    span = {
        position: sum(
            lengths[
                heading.index + 1 : headings[position + 1].index
                if position + 1 < len(headings)
                else len(paragraphs)
            ]
        )
        for position, heading in enumerate(headings)
    }
    chosen = _choose(headings, span, family) if headings else []

    def whole_document() -> list[Section]:
        text = "\n\n".join(paragraphs)
        return [Section("document", None, None, fallback_title[:200], 0, text)] if text else []

    minimum = 1 if family == "8-K" else 2
    if len(chosen) < minimum:
        return whole_document()

    sections: list[Section] = []
    for ordinal, heading in enumerate(chosen):
        end = chosen[ordinal + 1].index if ordinal + 1 < len(chosen) else len(paragraphs)
        body = [
            paragraphs[i]
            for i in range(heading.index + 1, end)
            if i not in repeated[heading.key]
            # Part headings ("PART II") belong to no item, wherever running headers put them.
            and not (lengths[i] <= 60 and _PART.match(_heading_text(paragraphs[i])))
        ]
        if body and not heading.rest and _is_title_line(body[0], heading.title):
            body = body[1:]  # "Item 1." and "Business" on separate lines
        if ordinal + 1 == len(chosen):
            # The signature block follows the last item; it is not part of it.
            for cut, paragraph in enumerate(body):
                if _SIGNATURES.match(_heading_text(paragraph)):
                    body = body[:cut]
                    break
        prefix = f"Item {heading.item}"
        title = (
            f"{heading.part} · {prefix}. {heading.title}"
            if heading.part
            else f"{prefix}. {heading.title}"
        )
        sections.append(
            Section(
                heading.key, heading.part, heading.item, title[:200], ordinal, "\n\n".join(body)
            )
        )
    total = sum(len(s.text) for s in sections)
    if any(s.key in _TRAILING_ITEMS and len(s.text) > max(total / 2, 20_000) for s in sections):
        return whole_document()
    mdna = next((s for s in sections if s.key == _MDNA.get(family)), None)
    largest = max(len(s.text) for s in sections)
    if (
        family in _MDNA
        and total > 50_000
        and largest > total / 2
        and (mdna is None or len(mdna.text) < 1_000)
    ):
        return whole_document()
    return sections
