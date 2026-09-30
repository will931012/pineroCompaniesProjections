"""Deterministic event classification.

SEC 8-K items map to event types by the item's legal meaning. News headlines are matched
against ordered keyword rules; the first rule that matches wins, and the matched words are
kept as evidence so every label can be checked. Bump CLASSIFIER_VERSION when rules change.
"""

import re
from dataclasses import dataclass

CLASSIFIER_VERSION = "2026.09-3"

EVENT_TYPES: dict[str, str] = {
    "bankruptcy": "Bankruptcy",
    "restatement": "Accounting restatement",
    "cybersecurity": "Cybersecurity incident",
    "m_and_a": "Mergers & acquisitions",
    "earnings": "Earnings results",
    "guidance": "Guidance",
    "leadership": "Leadership change",
    "material_agreement": "Material agreement",
    "financing": "Debt & equity financing",
    "capital_return": "Dividends & buybacks",
    "restructuring": "Restructuring & impairments",
    "listing": "Listing & delisting",
    "auditor_change": "Auditor change",
    "governance": "Governance & shareholder votes",
    "legal_regulatory": "Legal & regulatory",
    "analyst": "Analyst rating",
    "product": "Products & operations",
    "disclosure": "Other disclosure",
    "other": "Other news",
}

# 8-K items (Form 8-K General Instructions B) to event types.
EIGHT_K_TYPES: dict[str, str] = {
    "1.01": "material_agreement",
    "1.02": "material_agreement",
    "1.03": "bankruptcy",
    "1.05": "cybersecurity",
    "2.01": "m_and_a",
    "2.02": "earnings",
    "2.03": "financing",
    "2.04": "financing",
    "2.05": "restructuring",
    "2.06": "restructuring",
    "3.01": "listing",
    "3.02": "financing",
    "3.03": "governance",
    "4.01": "auditor_change",
    "4.02": "restatement",
    "5.01": "m_and_a",
    "5.02": "leadership",
    "5.03": "governance",
    "5.07": "governance",
    "7.01": "disclosure",
    "8.01": "disclosure",
}
# When an 8-K reports several items, the most consequential one names the event.
_PRIORITY = list(EVENT_TYPES)

# Ordered: the first matching rule wins (a "bankruptcy" headline is not "legal").
_NEWS_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("bankruptcy", re.compile(r"\b(chapter 11|bankrupt\w*|insolven\w*)\b", re.I)),
    ("restatement", re.compile(r"\b(restat(e|es|ed|ement)|accounting error)\b", re.I)),
    ("cybersecurity", re.compile(
        r"\b(cyber ?attack|data breach|ransomware|hack(ed|ers?)?)\b", re.I)),
    ("m_and_a", re.compile(
        r"\b(acquir(e|es|ed|ing)|acquisition|merger|merg(e|es|ed|ing)|takeover|buyout|"
        r"divest\w*|spin[- ]?off)\b", re.I)),
    ("earnings", re.compile(
        r"\b(earnings|quarterly (results|profit|revenue)|(first|second|third|fourth)[- ]quarter"
        r" (results|profit|revenue|sales)|q[1-4] (results|earnings|revenue|sales)|"
        r"(beats|misses|tops) (estimates|expectations))\b", re.I)),
    ("guidance", re.compile(r"\b(guidance|outlook|forecasts?)\b", re.I)),
    # A title alone ("… as CEO") is not a change; a change verb is required.
    ("leadership", re.compile(
        r"\b(resign(s|ed|ation)?|step(s|ped)? down|to step down|retir(e|es|ed|ement)|"
        r"appoint(s|ed|ment)?|names? (a )?new|successor|succeed(s|ed)?|ousted|fired|"
        r"hires? (a )?new)\b", re.I)),
    ("capital_return", re.compile(
        r"\b(dividends?|buybacks?|share repurchases?|repurchase program)\b", re.I)),
    ("financing", re.compile(
        r"\b(bond (sale|offering)|notes offering|debt offering|(secondary|share|stock) offering|"
        r"credit facility|raises \$)", re.I)),
    ("legal_regulatory", re.compile(
        r"\b(lawsuit|sues|sued|settle(s|d|ment)|antitrust|probe|investigation|fined|"
        r"regulators?|ftc|doj|court|ruling|verdict|subpoena)\b", re.I)),
    ("analyst", re.compile(
        # "Upgrade" alone is often a product ("platform upgrade"); an analyst upgrade has a rating.
        r"\b((up|down)grade[sd]? .{0,40}?\b(to|at) (buy|sell|hold|neutral|overweight|underweight|"
        r"outperform|underperform|equal[- ]weight|market perform)|price target|"
        r"initiates? coverage|overweight|underweight|outperform|underperform)\b", re.I)),
    ("product", re.compile(r"\b(launch(es|ed)?|unveil(s|ed)?|recalls?|rolls? out)\b", re.I)),
]  # fmt: skip


@dataclass(frozen=True)
class Classification:
    event_type: str
    rule: str
    matched: str | None


def classify_eight_k(items: list[str]) -> Classification:
    types = sorted(
        {EIGHT_K_TYPES[item] for item in items if item in EIGHT_K_TYPES},
        key=_PRIORITY.index,
    )
    if not types:
        return Classification("disclosure", "8-K items", None)
    return Classification(types[0], "8-K items", ", ".join(items))


def classify_headline(title: str) -> Classification:
    for event_type, pattern in _NEWS_RULES:
        match = pattern.search(title)
        if match:
            return Classification(event_type, f"headline:{event_type}", match.group(0))
    return Classification("other", "headline:none", None)
