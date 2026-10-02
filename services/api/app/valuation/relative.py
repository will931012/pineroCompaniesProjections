"""Relative valuation: the company's multiples against peers, its industry group, and its past.

Medians use only positive multiples (a negative P/E says nothing about price). The implied
value applies the peer median to the company's own latest-basis figure (TTM or fiscal year).
Historical multiples use the close nearest each fiscal year end (within a week) and that
year's weighted diluted shares; they need stored price history.
"""

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Company, CompanyMetric
from app.fundamentals.metrics import Context
from app.fundamentals.peers import select_peers
from app.fundamentals.service import load_facts, load_prices
from app.fundamentals.snapshot import PricePoint
from app.fundamentals.statements import StatementSet, build_statements, shares_for_market_cap
from app.valuation.schemas import MultipleOut
from app.valuation.service import Latest


@dataclass(frozen=True)
class MultipleDef:
    key: str
    label: str
    denominator: str


MULTIPLES = (
    MultipleDef("pe_ratio", "P/E", "net income"),
    MultipleDef("ev_to_ebitda", "EV / EBITDA", "EBITDA"),
    MultipleDef("price_to_sales", "P/S", "revenue"),
    MultipleDef("price_to_book", "P/B", "shareholders' equity"),
)


def _median(values: list[float]) -> float | None:
    positive = [v for v in values if v > 0]
    return statistics.median(positive) if positive else None


def _metric_values(db: Session, key: str, company_ids: list[int]) -> list[float]:
    if not company_ids:
        return []
    return [
        float(v)
        for v in db.scalars(
            select(CompanyMetric.value).where(
                CompanyMetric.metric == key, CompanyMetric.company_id.in_(company_ids)
            )
        )
    ]


def relative_valuation(
    db: Session, company: Company
) -> tuple[str, str | None, list[int], list[MultipleOut], float | None]:
    facts = load_facts(db, company)
    annual = build_statements(facts, "annual")
    quarterly = build_statements(facts, "quarterly")
    latest = Latest(annual, quarterly)
    prices = load_prices(db, company)
    shares_count = shares_for_market_cap(facts, None, prices[-1].trade_date) if prices else None
    shares = float(shares_count.value) if shares_count else None

    def figure(item: str) -> float | None:
        if item == "ebitda":
            return latest.value(lambda c: c.ebitda())[0]
        return latest.item(item)[0]

    debt = latest.value(lambda c: c.debt())[0] or 0.0
    cash = figure("cash") or 0.0
    peer_basis, peer_ids = select_peers(db, company, [], 10)
    industry_ids: list[int] = []
    industry_basis = None
    if company.sic_code:
        industry_basis = f"SIC major group {company.sic_code[:2]}"
        industry_ids = list(
            db.scalars(
                select(Company.id).where(
                    func.left(Company.sic_code, 2) == company.sic_code[:2], Company.id != company.id
                )
            )
        )

    def _per_share(value: float, count: float | None, item: str) -> float | None:
        base = figure(item)
        if count is None or count <= 0 or base is None or base <= 0:
            return None
        return value / count

    implied: dict[str, Callable[[float], float | None]] = {
        "pe_ratio": lambda m: _per_share(m * (figure("net_income") or 0), shares, "net_income"),
        "ev_to_ebitda": lambda m: _per_share(m * (figure("ebitda") or 0) - debt + cash, shares,
                                             "ebitda"),
        "price_to_sales": lambda m: _per_share(m * (figure("revenue") or 0), shares, "revenue"),
        "price_to_book": lambda m: _per_share(m * (figure("total_equity") or 0), shares,
                                              "total_equity"),
    }  # fmt: skip

    history = _history(annual, prices)
    company_metrics = {
        m.metric: float(m.value)
        for m in db.scalars(select(CompanyMetric).where(CompanyMetric.company_id == company.id))
    }
    rows = []
    for definition in MULTIPLES:
        peers = _metric_values(db, definition.key, peer_ids)
        industry = _metric_values(db, definition.key, industry_ids)
        peer_median = _median(peers)
        past = history.get(definition.key, [])
        rows.append(
            MultipleOut(
                key=definition.key,
                label=definition.label,
                company=company_metrics.get(definition.key),
                peer_median=peer_median,
                peer_count=len([v for v in peers if v > 0]),
                industry_median=_median(industry),
                industry_count=len([v for v in industry if v > 0]),
                history_median=_median(past),
                history_years=len(past),
                implied_per_share=implied[definition.key](peer_median) if peer_median else None,
                denominator=definition.denominator,
            )
        )
    price = float(prices[-1].close) if prices else None
    return peer_basis, industry_basis, peer_ids, rows, price


def _history(annual: StatementSet, prices: list[PricePoint]) -> dict[str, list[float]]:
    """Multiples at the last five fiscal year ends, from the close nearest each year end."""
    if not prices:
        return {}
    result: dict[str, list[float]] = {}
    for index, period in enumerate(annual.periods[-5:], start=len(annual.periods) - 5):
        if index < 0:
            continue
        near = [p for p in prices if period.end - timedelta(days=7) <= p.trade_date <= period.end]
        shares = annual.get("shares_diluted", period.key)
        if not near or shares is None or shares <= 0:
            continue
        cap = near[-1].close * shares
        ctx = Context(annual, index)
        debt = ctx.debt() or Decimal(0)
        cash = ctx.cur("cash") or Decimal(0)
        for key, denominator, numerator in (
            ("pe_ratio", ctx.cur("net_income"), cap),
            ("ev_to_ebitda", ctx.ebitda(), cap + debt - cash),
            ("price_to_sales", ctx.cur("revenue"), cap),
            ("price_to_book", ctx.cur("total_equity"), cap),
        ):
            if denominator is not None and denominator > 0:
                result.setdefault(key, []).append(float(numerator / denominator))
    return result
