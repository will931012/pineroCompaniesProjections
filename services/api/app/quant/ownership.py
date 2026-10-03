"""Institutional ownership from SEC's quarterly Form 13F data sets.

- Each data set covers filings made in a three-month window; holdings are kept only for the
  report period most filings in it cover (e.g. 30 June for the June–August file).
- Per manager: the latest original 13F-HR, replaced by the latest RESTATEMENT amendment when
  one exists, plus any NEW HOLDINGS amendments filed after it.
- Put and call rows are excluded; share totals count only rows reported in shares (SH).
- VALUE has been reported in whole dollars since 3 January 2023 and in thousands before.
- CUSIPs are mapped to listings with OpenFIGI, largest reported value first, and linked to a
  company when the ticker is in our directory. 13F lists long positions of managers above
  $100 million; it omits shorts and smaller holders.
"""

import csv
import io
import logging
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import CusipMapping, InstitutionalHolding, OwnershipSummary, Security
from app.providers.base import ProviderError, record_fetch
from app.providers.openfigi import OpenFigiClient
from app.providers.sec_edgar import SecEdgarClient

logger = logging.getLogger(__name__)

TOP_CUSIPS = 2000
TOP_HOLDERS = 100
DOLLAR_VALUES_FROM = date(2023, 1, 3)
csv.field_size_limit(1_000_000)


def _day(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with archive.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


@dataclass
class Filing:
    accession: str
    cik: int
    filed: date
    period: date
    kind: str  # original | restatement | new_holdings
    manager: str = ""


@dataclass
class Parsed:
    period: date
    filings: dict[str, Filing]
    cusip_value: Counter[str] = field(default_factory=Counter)
    cusip_name: dict[str, str] = field(default_factory=dict)
    rejected: int = 0


def select_filings(filings: list[Filing]) -> dict[str, Filing]:
    """The accessions that make up each manager's holdings for the period."""
    by_manager: dict[int, list[Filing]] = defaultdict(list)
    for f in filings:
        by_manager[f.cik].append(f)
    chosen: dict[str, Filing] = {}
    for items in by_manager.values():
        restatements = sorted((f for f in items if f.kind == "restatement"), key=lambda f: f.filed)
        originals = sorted((f for f in items if f.kind == "original"), key=lambda f: f.filed)
        base = restatements[-1] if restatements else (originals[-1] if originals else None)
        if base is None:
            continue
        chosen[base.accession] = base
        for f in items:
            if f.kind == "new_holdings" and f.filed >= base.filed:
                chosen[f.accession] = f
    return chosen


def parse_filings(archive: zipfile.ZipFile) -> tuple[date, dict[str, Filing]]:
    submissions: dict[str, Filing] = {}
    for row in _rows(archive, "SUBMISSION.tsv"):
        kind = row.get("SUBMISSIONTYPE", "")
        filed, period = _day(row.get("FILING_DATE", "")), _day(row.get("PERIODOFREPORT", ""))
        if kind not in {"13F-HR", "13F-HR/A"} or filed is None or period is None:
            continue
        try:
            cik = int(row["CIK"])
        except (KeyError, ValueError):
            continue
        submissions[row["ACCESSION_NUMBER"]] = Filing(
            row["ACCESSION_NUMBER"], cik, filed, period,
            "original" if kind == "13F-HR" else "pending",
        )  # fmt: skip
    for row in _rows(archive, "COVERPAGE.tsv"):
        filing = submissions.get(row.get("ACCESSION_NUMBER", ""))
        if filing is None:
            continue
        filing.manager = (row.get("FILINGMANAGER_NAME") or "").strip()[:200]
        if filing.kind == "pending":
            amendment = (row.get("AMENDMENTTYPE") or "").upper()
            filing.kind = "restatement" if "RESTATEMENT" in amendment else "new_holdings"
    period = Counter(f.period for f in submissions.values() if f.kind == "original").most_common(1)[
        0
    ][0]
    current = [f for f in submissions.values() if f.period == period and f.kind != "pending"]
    return period, select_filings(current)


@dataclass(frozen=True)
class Position:
    shares: Decimal
    value: Decimal


def read_holdings(
    archive: zipfile.ZipFile, filings: dict[str, Filing], cusips: set[str] | None
) -> tuple[Counter[str], dict[str, str], dict[tuple[str, int], Position], int]:
    """Total value per CUSIP, issuer names, and (when `cusips` is given) each manager's position."""
    totals: Counter[str] = Counter()
    names: dict[str, str] = {}
    positions: dict[tuple[str, int], Position] = {}
    rejected = 0
    for row in _rows(archive, "INFOTABLE.tsv"):
        filing = filings.get(row.get("ACCESSION_NUMBER", ""))
        if filing is None or (row.get("PUTCALL") or "").strip():
            continue
        cusip = (row.get("CUSIP") or "").strip().upper()
        try:
            value = Decimal(row["VALUE"])
            amount = Decimal(row["SSHPRNAMT"])
        except (KeyError, ArithmeticError, ValueError):
            rejected += 1
            continue
        if len(cusip) != 9 or value < 0:
            rejected += 1
            continue
        if filing.filed < DOLLAR_VALUES_FROM:
            value *= 1000
        shares = amount if (row.get("SSHPRNAMTTYPE") or "").strip() == "SH" else Decimal(0)
        totals[cusip] += int(value)
        names.setdefault(cusip, (row.get("NAMEOFISSUER") or "").strip()[:200])
        if cusips is not None and cusip in cusips:
            key = (cusip, filing.cik)
            current = positions.get(key)
            positions[key] = Position(
                (current.shares if current else Decimal(0)) + shares,
                (current.value if current else Decimal(0)) + value,
            )
    return totals, names, positions, rejected


PRICE_OUTLIER = Decimal(5)


def drop_price_outliers(
    positions: dict[tuple[str, int], Position],
) -> tuple[dict[tuple[str, int], Position], int]:
    """Drop positions whose value per share is over 5× from the CUSIP's median.

    Managers occasionally file values in thousands or put one share class's count under
    another class's CUSIP (seen: 368,743 Berkshire shares reported at $264.6 billion). Every
    holder of a CUSIP marks it at the same quarter-end price, so the median is reliable.
    """
    prices: dict[str, list[Decimal]] = defaultdict(list)
    for (cusip, _), p in positions.items():
        if p.shares > 0:
            prices[cusip].append(p.value / p.shares)
    medians = {c: sorted(v)[len(v) // 2] for c, v in prices.items() if len(v) >= 5}
    kept: dict[tuple[str, int], Position] = {}
    dropped = 0
    for key, p in positions.items():
        median = medians.get(key[0])
        if median and median > 0 and p.shares > 0:
            ratio = (p.value / p.shares) / median
            if ratio > PRICE_OUTLIER or ratio < 1 / PRICE_OUTLIER:
                dropped += 1
                continue
        kept[key] = p
    return kept, dropped


def map_cusips(
    db: Session, client: OpenFigiClient, cusips: list[str], fetch_limit: int | None = None
) -> int:
    """Map CUSIPs without a stored mapping; returns how many were requested."""
    known = set(db.scalars(select(CusipMapping.cusip).where(CusipMapping.cusip.in_(cusips))))
    todo = [c for c in cusips if c not in known][:fetch_limit]
    failed = 0
    tickers = {
        s.ticker: s.company_id for s in db.scalars(select(Security).where(Security.is_active))
    }
    for i in range(0, len(todo), client.batch_size):
        batch = todo[i : i + client.batch_size]
        result = None
        for _attempt in range(2):
            try:
                result = client.map_cusips(batch)
                break
            except ProviderError as error:
                if error.meta is not None:
                    record_fetch(db, error.meta, status="error", error=error)
                    db.commit()
                if not error.retryable or error.code == "provider_rate_limited":
                    raise
        if result is None:
            # Left unmapped; the next run retries these CUSIPs.
            failed += len(batch)
            continue
        fetch = record_fetch(db, result.meta, status="success", record_count=len(result.data))
        rows = []
        for m in result.data:
            # Debt and preferred lines come back with descriptions as "tickers"; clip to size.
            ticker = m.ticker.replace("/", "-").upper()[:20] if m.ticker else None
            rows.append(
                {
                    "cusip": m.cusip,
                    "status": "mapped" if m.figi else "no_match",
                    "figi": m.figi,
                    "composite_figi": m.composite_figi,
                    "ticker": ticker,
                    "exch_code": (m.exch_code or "")[:10] or None,
                    "name": m.name,
                    "security_type": (m.security_type or "")[:60] or None,
                    # Only the US composite listing identifies the company; a CUSIP retired in a
                    # reorganisation can map to an unrelated foreign line (e.g. old Exxon → EXMOC).
                    "company_id": tickers.get(ticker) if ticker and m.exch_code == "US" else None,
                    "fetch_id": fetch.id,
                }
            )
        db.execute(insert(CusipMapping).values(rows).on_conflict_do_nothing())
        db.commit()
    if failed:
        logger.warning("cusip_mapping_incomplete", extra={"unmapped": failed})
    return len(todo) - failed


def refresh_ownership(
    db: Session,
    sec: SecEdgarClient,
    figi: OpenFigiClient,
    *,
    datasets: int = 2,
    top_cusips: int = TOP_CUSIPS,
) -> list[dict[str, Any]]:
    """Load the newest `datasets` quarterly files not loaded yet."""
    links = sec.fetch_13f_dataset_links()
    record_fetch(db, links.meta, status="success", record_count=len(links.data))
    db.commit()
    reports = []
    loaded = set(db.scalars(select(OwnershipSummary.dataset).distinct()))
    for path in links.data[:datasets]:
        name = path.rsplit("/", 1)[1]
        if name in loaded:
            reports.append({"dataset": name, "skipped": "already loaded"})
            continue
        reports.append(load_dataset(db, sec, figi, path, top_cusips))
    return reports


def load_dataset(
    db: Session, sec: SecEdgarClient, figi: OpenFigiClient, path: str, top_cusips: int
) -> dict[str, Any]:
    name = path.rsplit("/", 1)[1]
    try:
        result = sec.fetch_13f_dataset(path)
    except ProviderError as error:
        if error.meta is not None:
            record_fetch(db, error.meta, status="error", error=error)
            db.commit()
        raise
    archive = zipfile.ZipFile(io.BytesIO(result.data))
    period, filings = parse_filings(archive)
    totals, _names, _, rejected = read_holdings(archive, filings, None)
    fetch = record_fetch(
        db, result.meta, status="success", record_count=len(filings), rejected_count=rejected
    )
    db.commit()
    largest = [c for c, _ in totals.most_common(top_cusips)]
    requested = map_cusips(db, figi, largest)
    company_of = {
        m.cusip: m.company_id
        for m in db.scalars(
            select(CusipMapping).where(
                CusipMapping.cusip.in_(largest), CusipMapping.company_id.is_not(None)
            )
        )
        if m.company_id is not None
    }
    _, _, raw_positions, _ = read_holdings(archive, filings, set(company_of))
    positions, outliers = drop_price_outliers(raw_positions)
    by_company: dict[int, dict[int, Position]] = defaultdict(dict)
    cusips_of: dict[int, set[str]] = defaultdict(set)
    for (cusip, cik), position in positions.items():
        company = company_of[cusip]
        cusips_of[company].add(cusip)
        current = by_company[company].get(cik)
        by_company[company][cik] = Position(
            (current.shares if current else Decimal(0)) + position.shares,
            (current.value if current else Decimal(0)) + position.value,
        )
    managers = {f.cik: (f.manager, f.accession, f.filed) for f in filings.values()}
    companies = list(by_company)
    db.execute(
        delete(OwnershipSummary).where(
            OwnershipSummary.period_of_report == period, OwnershipSummary.company_id.in_(companies)
        )
    )
    db.execute(
        delete(InstitutionalHolding).where(
            InstitutionalHolding.period_of_report == period,
            InstitutionalHolding.company_id.in_(companies),
        )
    )
    summaries, holdings = [], []
    for company, holders in by_company.items():
        summaries.append(
            {
                "company_id": company, "period_of_report": period, "holders": len(holders),
                "shares": sum((p.shares for p in holders.values()), Decimal(0)),
                "value_usd": sum((p.value for p in holders.values()), Decimal(0)),
                "cusips": sorted(cusips_of[company]), "dataset": name, "fetch_id": fetch.id,
            }
        )  # fmt: skip
        top = sorted(holders.items(), key=lambda kv: -kv[1].value)[:TOP_HOLDERS]
        for cik, position in top:
            manager, accession, filed = managers.get(cik, ("", "", period))
            holdings.append(
                {
                    "company_id": company, "period_of_report": period, "filer_cik": cik,
                    "filer_name": manager or f"CIK {cik}", "accession": accession,
                    "filing_date": filed, "shares": position.shares, "value_usd": position.value,
                    "fetch_id": fetch.id,
                }
            )  # fmt: skip
    for i in range(0, len(summaries), 2000):
        db.execute(insert(OwnershipSummary).values(summaries[i : i + 2000]))
    for i in range(0, len(holdings), 4000):
        db.execute(insert(InstitutionalHolding).values(holdings[i : i + 4000]))
    db.commit()
    return {
        "dataset": name, "period": period.isoformat(), "filings": len(filings),
        "cusips_mapped_now": requested, "companies": len(companies), "rejected_rows": rejected,
        "price_outliers_dropped": outliers,
    }  # fmt: skip
