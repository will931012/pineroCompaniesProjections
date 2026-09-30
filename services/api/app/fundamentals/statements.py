"""Build annual and quarterly statements from point-in-time XBRL facts.

Rules (all deterministic):
- Only facts public on or before `as_of` are used; for each (concept, period) the most
  recently filed value wins, so restatements known at `as_of` apply and later ones don't.
- Fiscal years come from annual-duration facts (350–380 days). SEC's `fy` describes the
  filing, not the fact, so it labels a year only in the filing whose latest annual period
  that year is; years seen only as comparatives use the issuer's usual label offset.
- A fiscal year has quarters only when exactly three interim period ends are reported.
- After the last fiscal year, the 10-Q periods reported so far form an in-progress year
  (quarters only), so quarterly statements and TTM reach the latest filing.
- Flow items: a reported three-month value is used directly; otherwise the quarter is
  derived from year-to-date values of the *same* concept (e.g. Q4 = FY − 9M YTD).
  Derived cells carry the formula used.
- Instant items take the value reported at the period-end date.
- Per-share and average-share items are never differenced; only reported values appear.
- Periods are identified by dates reported in filings, never inferred from calendars.
- Stock splits: when a later filing restates a share count for the same period by a split
  ratio (2, 3, 1.5, 1/10 …), values filed before that filing are put on the new basis
  (shares × ratio, per-share ÷ ratio) and say so, so per-share history stays comparable.
  Ratios of 1000 are not treated as splits: they come from filings that tagged share
  counts in thousands, and fixing those needs evidence that deduplicated storage lacks.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Literal, Protocol

from app.fundamentals.concepts import LINE_ITEMS, SHARES_OUTSTANDING, LineItem

PeriodType = Literal["annual", "quarterly"]

ANNUAL_DAYS = range(350, 381)
QUARTER_DAYS = range(80, 101)


class FactLike(Protocol):
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed_date: date


@dataclass
class AdjustedFact:
    """A fact whose value was restated onto a later share basis (after a split)."""

    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed_date: date
    adjustment: str


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    fiscal_year: int
    fiscal_quarter: int | None
    start: date
    end: date


@dataclass(frozen=True)
class Cell:
    value: Decimal
    concept: str | None
    accession: str | None
    filed_date: date
    derivation: str | None = None


@dataclass
class StatementSet:
    period_type: PeriodType
    periods: list[Period]
    cells: dict[str, dict[str, Cell]] = field(default_factory=dict)

    def get(self, item: str, period_key: str) -> Decimal | None:
        cell = self.cells.get(item, {}).get(period_key)
        return cell.value if cell else None


@dataclass(frozen=True)
class FiscalYear:
    fiscal_year: int
    start: date
    # Fiscal year end; for an in-progress year, the latest reported quarter end.
    end: date
    # Interim quarter ends: three for a complete year, one to three for an in-progress one.
    quarter_ends: tuple[date, ...] | None
    complete: bool = True

    @property
    def period_ends(self) -> tuple[date, ...]:
        """End date of each quarter the year has, in order."""
        assert self.quarter_ends is not None
        return (*self.quarter_ends, self.end) if self.complete else self.quarter_ends


def _days(start: date, end: date) -> int:
    return (end - start).days + 1


def _next_day(day: date) -> date:
    return date.fromordinal(day.toordinal() + 1)


def _base_year(end: date) -> int:
    # 52/53-week years can end a few days into January; shift so they share a base year.
    return date.fromordinal(end.toordinal() - 14).year


@dataclass(frozen=True)
class SplitEvent:
    # First filing that reported share counts on the new basis.
    effective: date
    # New shares per old share: 4 for a 4-for-1 split, 0.1 for a 1-for-10 reverse split.
    ratio: Decimal


def _split_ratio(before: Decimal, after: Decimal) -> Decimal | None:
    """The split ratio a restatement implies, if it is one (within 2%), else None."""
    if before <= 0 or after <= 0:
        return None
    change = float(after / before)
    forward = change >= 1
    size = change if forward else 1 / change
    for ratio in (1.5, *range(2, 101)):
        if abs(size - ratio) <= 0.02 * ratio:
            exact = Decimal(str(ratio))
            return exact if forward else 1 / exact
    return None


def split_events(facts: Iterable[FactLike], as_of: date | None) -> list[SplitEvent]:
    """Splits evidenced by restated us-gaap share counts for the same period."""
    groups: dict[tuple[str, date | None, date], list[FactLike]] = defaultdict(list)
    for fact in facts:
        if (
            fact.taxonomy == "us-gaap"
            and fact.unit == "shares"
            and (as_of is None or fact.filed_date <= as_of)
        ):
            groups[(fact.concept, fact.period_start, fact.period_end)].append(fact)
    found: list[tuple[date, Decimal]] = []
    for members in groups.values():
        members.sort(key=lambda f: (f.filed_date, f.accession))
        for before, after in pairwise(members):
            ratio = _split_ratio(before.value, after.value)
            if ratio is not None:
                found.append((after.filed_date, ratio))
    events: list[SplitEvent] = []
    for effective, ratio in sorted(found):
        # The same split shows up in many concepts and periods; keep its first filing.
        if any(e.ratio == ratio and (effective - e.effective).days <= 400 for e in events):
            continue
        events.append(SplitEvent(effective, ratio))
    return events


def split_adjusted(facts: Iterable[FactLike], as_of: date | None) -> list[FactLike]:
    """Put share and per-share values filed before a split onto the post-split basis."""
    facts = list(facts)
    events = split_events(facts, as_of)
    if not events:
        return facts
    names = [f.name for f in fields(AdjustedFact) if f.name != "adjustment"]
    result: list[FactLike] = []
    for fact in facts:
        later = [e for e in events if fact.filed_date < e.effective]
        if not later or fact.unit not in ("shares", "USD/shares"):
            result.append(fact)
            continue
        ratio = Decimal(1)
        for event in later:
            ratio *= event.ratio
        values = {name: getattr(fact, name) for name in names}
        values["value"] = fact.value * ratio if fact.unit == "shares" else fact.value / ratio
        notes = ", ".join(
            f"split ×{e.ratio.normalize():f} from {e.effective.isoformat()}" for e in later
        )
        result.append(AdjustedFact(**values, adjustment=f"Adjusted ({notes})"))
    return result


def latest_known(facts: Iterable[FactLike], as_of: date | None) -> list[FactLike]:
    """For each (taxonomy, concept, unit, start, end) keep the latest value filed by as_of."""

    chosen: dict[tuple, FactLike] = {}
    for fact in facts:
        if as_of is not None and fact.filed_date > as_of:
            continue
        key = (
            fact.taxonomy,
            fact.concept,
            fact.unit,
            fact.period_start,
            fact.period_end,
        )
        current = chosen.get(key)
        if current is None or (fact.filed_date, fact.accession) > (
            current.filed_date,
            current.accession,
        ):
            chosen[key] = fact
    return list(chosen.values())


def _interim_ends(facts: Sequence[FactLike], start: date, before: date | None) -> list[date]:
    """Distinct ends of year-to-date or three-month spans inside a fiscal year."""
    return sorted(
        {
            f.period_end
            for f in facts
            if f.period_start is not None
            and start <= f.period_start
            and start < f.period_end
            and (f.period_end < before if before else _days(start, f.period_end) < 350)
            and (f.period_start == start or _days(f.period_start, f.period_end) in QUARTER_DAYS)
        }
    )


def _year_labels(facts: Sequence[FactLike], ends: Sequence[date]) -> dict[date, int]:
    """Fiscal-year label for each annual period end.

    A filing's `fy` is trustworthy only for the latest annual period that filing reports
    (its own fiscal year); earlier years in it are comparatives carrying the same `fy`.
    """
    current_end: dict[str, date] = {}
    for fact in facts:
        if fact.fiscal_period == "FY" and fact.fiscal_year is not None and fact.period_start:
            if _days(fact.period_start, fact.period_end) in ANNUAL_DAYS:
                known = current_end.get(fact.accession)
                if known is None or fact.period_end > known:
                    current_end[fact.accession] = fact.period_end
    own: dict[date, tuple[date, int]] = {}
    for fact in facts:
        if fact.fiscal_year is None or current_end.get(fact.accession) != fact.period_end:
            continue
        known_label = own.get(fact.period_end)
        if known_label is None or fact.filed_date < known_label[0]:
            own[fact.period_end] = (fact.filed_date, fact.fiscal_year)

    offsets = Counter(label - _base_year(end) for end, (_, label) in own.items())
    offset = offsets.most_common(1)[0][0] if offsets else 0
    fallback = {end: _base_year(end) + offset for end in ends}
    labels = {end: own[end][1] if end in own else fallback[end] for end in ends}
    # Inconsistent tagging can still collide; consistent derived labels are safer then.
    return labels if len(set(labels.values())) == len(labels) else fallback


def _in_progress_year(facts: Sequence[FactLike], last: FiscalYear) -> FiscalYear | None:
    """Quarters reported after the last fiscal year end, if they chain quarter by quarter."""
    start = _next_day(last.end)
    ends = _interim_ends(facts, start, None)
    if not 1 <= len(ends) <= 3:
        return None
    previous = start
    for end in ends:
        if _days(previous, end) not in QUARTER_DAYS:
            return None
        previous = _next_day(end)
    return FiscalYear(last.fiscal_year + 1, start, ends[-1], tuple(ends), complete=False)


def fiscal_years(facts: Sequence[FactLike]) -> list[FiscalYear]:
    annual_starts: dict[date, Counter[date]] = defaultdict(Counter)
    for fact in facts:
        if (
            fact.period_start is not None
            and _days(fact.period_start, fact.period_end) in ANNUAL_DAYS
        ):
            annual_starts[fact.period_end][fact.period_start] += 1

    spans: list[tuple[date, date]] = []
    for end in sorted(annual_starts):
        start = annual_starts[end].most_common(1)[0][0]
        if spans and start <= spans[-1][1]:
            continue  # overlapping span (e.g. transition period); keep the earlier year
        spans.append((start, end))

    labels = _year_labels(facts, [end for _, end in spans])
    years: list[FiscalYear] = []
    for start, end in spans:
        interim_ends = _interim_ends(facts, start, end)
        quarter_ends = tuple(interim_ends) if len(interim_ends) == 3 else None
        years.append(FiscalYear(labels[end], start, end, quarter_ends))
    if years:
        in_progress = _in_progress_year(facts, years[-1])
        if in_progress is not None:
            years.append(in_progress)
    return years


class _Index:
    def __init__(self, facts: Sequence[FactLike]) -> None:
        self.duration: dict[tuple[str, str, date, date], FactLike] = {}
        self.instant: dict[tuple[str, str, date], FactLike] = {}
        self.by_end: dict[tuple[str, str, date], list[FactLike]] = defaultdict(list)
        for fact in facts:
            if fact.period_start is None:
                self.instant[(fact.taxonomy, fact.concept, fact.period_end)] = fact
            else:
                self.duration[(fact.taxonomy, fact.concept, fact.period_start, fact.period_end)] = (
                    fact
                )
                self.by_end[(fact.taxonomy, fact.concept, fact.period_end)].append(fact)

    def span(self, item: LineItem, concept: str, start: date, end: date) -> FactLike | None:
        return self.duration.get((item.taxonomy, concept, start, end))

    def quarter(self, item: LineItem, concept: str, end: date) -> FactLike | None:
        for fact in self.by_end.get((item.taxonomy, concept, end), []):
            assert fact.period_start is not None
            if _days(fact.period_start, end) in QUARTER_DAYS:
                return fact
        return None

    def at(self, item: LineItem, concept: str, end: date) -> FactLike | None:
        return self.instant.get((item.taxonomy, concept, end))


def _direct(fact: FactLike) -> Cell:
    return Cell(
        fact.value, fact.concept, fact.accession, fact.filed_date, getattr(fact, "adjustment", None)
    )


def _difference(concept: str, minuend: FactLike, subtrahend: FactLike, formula: str) -> Cell:
    return Cell(
        minuend.value - subtrahend.value,
        concept,
        minuend.accession,
        max(minuend.filed_date, subtrahend.filed_date),
        formula,
    )


def _annual_cell(index: _Index, item: LineItem, year: FiscalYear) -> Cell | None:
    for concept in item.concepts:
        fact = (
            index.at(item, concept, year.end)
            if item.kind == "instant"
            else index.span(item, concept, year.start, year.end)
        )
        if fact is not None:
            return _direct(fact)
    return None


def _quarter_cell(index: _Index, item: LineItem, year: FiscalYear, q: int) -> Cell | None:
    ends = year.period_ends
    end = ends[q - 1]
    for concept in item.concepts:
        if item.kind == "instant":
            fact = index.at(item, concept, end)
            if fact is not None:
                return _direct(fact)
            continue
        reported = index.quarter(item, concept, end)
        if reported is not None:
            return _direct(reported)
        if item.kind != "flow":
            continue
        if q == 1:
            ytd = index.span(item, concept, year.start, end)
            if ytd is not None:
                return _direct(ytd)
            continue
        prev_end = ends[q - 2]
        current = index.span(item, concept, year.start, end)
        previous = index.span(item, concept, year.start, prev_end)
        if current is not None and previous is not None:
            months = 3 * (q - 1)
            label = "FY" if q == 4 and year.complete else f"{3 * q}M YTD"
            return _difference(concept, current, previous, f"{label} − {months}M YTD")
    return None


def _with_gross_profit(cells: dict[str, dict[str, Cell]], period_key: str) -> None:
    if period_key in cells.get("gross_profit", {}):
        return
    revenue = cells.get("revenue", {}).get(period_key)
    cost = cells.get("cost_of_revenue", {}).get(period_key)
    if revenue and cost:
        cells.setdefault("gross_profit", {})[period_key] = Cell(
            revenue.value - cost.value,
            None,
            None,
            max(revenue.filed_date, cost.filed_date),
            "Revenue − Cost of revenue",
        )


def build_statements(
    facts: Sequence[FactLike], period_type: PeriodType, as_of: date | None = None
) -> StatementSet:
    known = latest_known(split_adjusted(facts, as_of), as_of)
    index = _Index(known)
    years = fiscal_years(known)
    result = StatementSet(period_type, [])

    for year in years:
        if period_type == "annual":
            if not year.complete:
                continue
            period = Period(
                f"FY{year.fiscal_year}",
                f"FY{year.fiscal_year}",
                year.fiscal_year,
                None,
                year.start,
                year.end,
            )
            result.periods.append(period)
            for item in LINE_ITEMS:
                cell = _annual_cell(index, item, year)
                if cell is not None:
                    result.cells.setdefault(item.key, {})[period.key] = cell
            _with_gross_profit(result.cells, period.key)
            continue
        if year.quarter_ends is None:
            continue
        ends = year.period_ends
        starts = (year.start, *(_next_day(end) for end in ends[:-1]))
        for q in range(1, len(ends) + 1):
            period = Period(
                f"FY{year.fiscal_year}-Q{q}",
                f"Q{q} FY{year.fiscal_year}",
                year.fiscal_year,
                q,
                starts[q - 1],
                ends[q - 1],
            )
            result.periods.append(period)
            for item in LINE_ITEMS:
                cell = _quarter_cell(index, item, year, q)
                if cell is not None:
                    result.cells.setdefault(item.key, {})[period.key] = cell
            _with_gross_profit(result.cells, period.key)

    # Drop trailing periods with no values at all (e.g. a fiscal year with only a balance date).
    populated = {key for values in result.cells.values() for key in values}
    result.periods = [p for p in result.periods if p.key in populated]
    return result


# A share count older than this (relative to the price it is multiplied by) is not used.
MAX_SHARE_COUNT_AGE_DAYS = 400


@dataclass(frozen=True)
class ShareCount:
    value: Decimal
    as_of: date
    filed_date: date
    accession: str
    classes: int
    source: str = "cover-page"


def latest_shares_outstanding(facts: Sequence[FactLike], as_of: date | None) -> ShareCount | None:
    """Latest cover-page share count. Multi-class issuers report one value per class in the
    same filing; those are summed and the number of classes is reported."""

    groups: dict[tuple[date, str], list[FactLike]] = defaultdict(list)
    for fact in facts:
        if (
            fact.taxonomy == SHARES_OUTSTANDING.taxonomy
            and fact.concept in SHARES_OUTSTANDING.concepts
            and (as_of is None or fact.filed_date <= as_of)
        ):
            groups[(fact.period_end, fact.accession)].append(fact)
    if not groups:
        return None
    (end, accession), members = max(groups.items(), key=lambda kv: (kv[0][0], kv[1][0].filed_date))
    adjusted = " split-adjusted" if any(hasattr(m, "adjustment") for m in members) else ""
    return ShareCount(
        sum((m.value for m in members), Decimal(0)),
        end,
        max(m.filed_date for m in members),
        accession,
        len(members),
        f"cover-page{adjusted}",
    )


def _latest_reported(
    facts: Sequence[FactLike], concept: str, as_of: date | None, source: str, *, quarter: bool
) -> ShareCount | None:
    """Latest value of a us-gaap share concept: an instant, or a three-month span."""
    candidates = [
        f
        for f in latest_known(facts, as_of)
        if f.taxonomy == "us-gaap"
        and f.concept == concept
        and (
            f.period_start is not None and _days(f.period_start, f.period_end) in QUARTER_DAYS
            if quarter
            else f.period_start is None
        )
    ]
    if not candidates:
        return None
    fact = max(candidates, key=lambda f: (f.period_end, f.filed_date))
    return ShareCount(fact.value, fact.period_end, fact.filed_date, fact.accession, 1, source)


def shares_for_market_cap(
    facts: Sequence[FactLike], as_of: date | None, reference: date
) -> ShareCount | None:
    """Share count to multiply a price by, from the first source that is recent enough.

    Order: cover-page shares outstanding; balance-sheet common shares outstanding; the latest
    quarter's weighted-average basic shares. Multi-class issuers (e.g. Alphabet, Meta) often
    report cover-page counts only per class, which companyfacts omits, hence the fallbacks.
    """
    facts = split_adjusted(facts, as_of)
    for count in (
        latest_shares_outstanding(facts, as_of),
        _latest_reported(
            facts, "CommonStockSharesOutstanding", as_of, "balance-sheet", quarter=False
        ),
        _latest_reported(
            facts,
            "WeightedAverageNumberOfSharesOutstandingBasic",
            as_of,
            "weighted-average basic",
            quarter=True,
        ),
    ):
        if (
            count is not None
            and count.value > 0
            and abs((reference - count.as_of).days) <= MAX_SHARE_COUNT_AGE_DAYS
        ):
            return count
    return None
