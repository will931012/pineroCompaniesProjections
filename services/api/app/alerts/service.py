"""Evaluate alert rules and deliver alerts by email.

A rule fires for things discovered after it was created (or last evaluated) and no older
than RECENT, so loading history never floods anyone. Each alert has a subject key unique per
rule (e.g. "event:812"), so re-running evaluation is harmless. Email delivery is a separate
step that records its outcome on every alert.
"""

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import (
    Alert,
    AlertRule,
    Company,
    DailyPrice,
    Event,
    Filing,
    InsiderTransaction,
    Security,
    User,
    Watchlist,
    WatchlistItem,
)
from app.events.taxonomy import EVENT_TYPES
from app.providers.email import EmailError, EmailSender

logger = logging.getLogger(__name__)

RECENT = timedelta(days=7)
RULE_KINDS = ("filing", "earnings", "news", "insider", "price")
DELIVERY_BATCH = 50


@dataclass(frozen=True)
class Candidate:
    subject_key: str
    company_id: int
    title: str
    body: str
    link: str | None
    occurred_at: datetime


def rule_company_ids(db: Session, rule: AlertRule) -> list[int]:
    tickers = [t.upper() for t in rule.tickers]
    ids = set(
        db.scalars(
            select(Security.company_id).where(Security.ticker.in_(tickers), Security.is_active)
        )
    )
    if rule.watchlist_id is not None:
        ids |= set(
            db.scalars(
                select(Security.company_id)
                .join(WatchlistItem, WatchlistItem.security_id == Security.id)
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(Watchlist.id == rule.watchlist_id, Watchlist.owner_id == rule.owner_id)
            )
        )
    return sorted(ids)


def tracked_company_ids(db: Session) -> list[int]:
    """Companies the worker keeps fresh: every watchlist entry and active rule's scope."""
    ids = set(
        db.scalars(
            select(Security.company_id).join(
                WatchlistItem, WatchlistItem.security_id == Security.id
            )
        )
    )
    for rule in db.scalars(select(AlertRule).where(AlertRule.active)):
        ids |= set(rule_company_ids(db, rule))
    return sorted(ids)


def _tickers(db: Session, company_ids: list[int]) -> dict[int, str]:
    result: dict[int, str] = {}
    for company_id, ticker in db.execute(
        select(Security.company_id, Security.ticker)
        .where(Security.company_id.in_(company_ids), Security.is_active)
        .order_by(Security.company_id, Security.id)
    ):
        result.setdefault(company_id, ticker)
    return result


def _money(value: Decimal) -> str:
    return f"${value:,.0f}"


def _event_candidates(
    db: Session, rule: AlertRule, ids: list[int], since: datetime, tickers: dict[int, str]
) -> list[Candidate]:
    query = select(Event).where(
        Event.company_id.in_(ids),
        Event.created_at > since,
        Event.occurred_at > utcnow() - RECENT,
    )
    params = rule.params
    if rule.kind == "earnings":
        query = query.where(Event.event_type == "earnings", Event.source_kind == "filing")
    else:
        query = query.where(Event.source_kind == "news")
        if params.get("event_types"):
            query = query.where(Event.event_type.in_(params["event_types"]))
        if params.get("first_only", True):
            query = query.where(Event.novelty == "first")
    candidates = []
    for event in db.scalars(query.order_by(Event.occurred_at)):
        ticker = tickers.get(event.company_id, "")
        kind = EVENT_TYPES.get(event.event_type, event.event_type)
        candidates.append(
            Candidate(
                f"event:{event.id}",
                event.company_id,
                f"{ticker}: {kind}",
                event.title,
                f"/companies/{ticker}?tab=news" if event.source_kind == "news" else
                f"/companies/{ticker}?tab=earnings",
                event.occurred_at,
            )
        )  # fmt: skip
    return candidates


def _filing_candidates(
    db: Session, rule: AlertRule, ids: list[int], since: datetime, tickers: dict[int, str]
) -> list[Candidate]:
    forms = rule.params.get("forms") or ["10-K", "10-Q", "8-K"]
    forms = sorted({*forms, *(f"{f}/A" for f in forms)})
    since_day = max(since, utcnow() - RECENT).date() - timedelta(days=1)
    rows = db.scalars(
        select(Filing)
        .where(Filing.company_id.in_(ids), Filing.form.in_(forms), Filing.filed_date >= since_day)
        .order_by(Filing.filed_date)
    )
    return [
        Candidate(
            f"filing:{f.id}",
            f.company_id,
            f"{tickers.get(f.company_id, '')}: new {f.form}",
            f"{f.form} filed {f.filed_date.isoformat()}"
            + (f" (items {f.items})" if f.items else "")
            + (f" — {f.description}" if f.description and f.description != f.form else ""),
            f"/companies/{tickers.get(f.company_id, '')}/filings/{f.accession}",
            datetime.combine(f.filed_date, datetime.min.time(), tzinfo=since.tzinfo),
        )
        for f in rows
    ]


def _insider_candidates(
    db: Session, rule: AlertRule, ids: list[int], since: datetime, tickers: dict[int, str]
) -> list[Candidate]:
    codes = rule.params.get("codes") or ["P"]
    minimum = Decimal(str(rule.params.get("min_value") or 0))
    rows = db.execute(
        select(InsiderTransaction, Filing)
        .join(Filing, Filing.id == InsiderTransaction.filing_id)
        .where(
            InsiderTransaction.company_id.in_(ids),
            InsiderTransaction.is_derivative.is_(False),
            InsiderTransaction.transaction_code.in_(codes),
            Filing.loaded_at > since,
            InsiderTransaction.transaction_date >= (utcnow() - timedelta(days=30)).date(),
        )
    ).all()
    candidates = []
    for tx, filing in rows:
        value = (tx.shares or Decimal(0)) * (tx.price or Decimal(0))
        if value < minimum:
            continue
        ticker = tickers.get(tx.company_id, "")
        verb = {"P": "bought", "S": "sold"}.get(tx.transaction_code or "", "reported")
        role = tx.officer_title or ("director" if tx.is_director else "insider")
        candidates.append(
            Candidate(
                f"insider:{tx.id}",
                tx.company_id,
                f"{ticker}: insider {verb} {_money(value)}",
                f"{tx.owner_name} ({role})"
                f" {verb} {tx.shares:,.0f} shares at ${tx.price} on {tx.transaction_date}"
                f" (Form {filing.form} filed {filing.filed_date}).",
                f"/companies/{ticker}?tab=insiders",
                datetime.combine(filing.filed_date, datetime.min.time(), tzinfo=since.tzinfo),
            )
        )  # fmt: skip
    return candidates


def _price_candidates(
    db: Session, rule: AlertRule, ids: list[int], since: datetime, tickers: dict[int, str]
) -> list[Candidate]:
    threshold = Decimal(str(rule.params.get("min_move_pct") or 5)) / 100
    candidates = []
    for company_id, ticker in tickers.items():
        bars = db.execute(
            select(DailyPrice.trade_date, DailyPrice.close, DailyPrice.security_id)
            .join(Security, Security.id == DailyPrice.security_id)
            .where(Security.company_id == company_id, Security.ticker == ticker)
            .order_by(DailyPrice.trade_date.desc())
            .limit(2)
        ).all()
        if len(bars) < 2 or bars[1].close <= 0:
            continue
        latest, previous = bars
        if latest.trade_date < (max(since, utcnow() - RECENT)).date() - timedelta(days=3):
            continue
        move = latest.close / previous.close - 1
        if abs(move) < threshold:
            continue
        candidates.append(
            Candidate(
                f"price:{latest.security_id}:{latest.trade_date.isoformat()}",
                company_id,
                f"{ticker}: {'up' if move > 0 else 'down'} {abs(move) * 100:.1f}%",
                f"{ticker} closed at {latest.close} on {latest.trade_date}, "
                f"{move * 100:+.2f}% from {previous.close} on {previous.trade_date}.",
                f"/companies/{ticker}",
                datetime.combine(latest.trade_date, datetime.min.time(), tzinfo=since.tzinfo),
            )
        )
    return candidates


_FINDERS = {
    "filing": _filing_candidates,
    "earnings": _event_candidates,
    "news": _event_candidates,
    "insider": _insider_candidates,
    "price": _price_candidates,
}


def evaluate_rule(db: Session, rule: AlertRule, email_ready: bool) -> int:
    """Create alerts for new matches; returns how many were created."""
    now = utcnow()
    since = rule.evaluated_at or rule.created_at
    ids = rule_company_ids(db, rule)
    created = 0
    if ids:
        tickers = _tickers(db, ids)
        status = "pending" if rule.email and email_ready else (
            "disabled" if not rule.email else "not_configured"
        )  # fmt: skip
        for c in _FINDERS[rule.kind](db, rule, ids, since, tickers):
            alert_id = db.scalar(
                insert(Alert)
                .values(
                    rule_id=rule.id,
                    owner_id=rule.owner_id,
                    company_id=c.company_id,
                    subject_key=c.subject_key[:120],
                    title=c.title[:300],
                    body=c.body,
                    link=c.link,
                    occurred_at=c.occurred_at,
                    created_at=now,
                    email_status=status,
                )
                .on_conflict_do_nothing()
                .returning(Alert.id)
            )
            created += alert_id is not None
    rule.evaluated_at = now
    db.commit()
    return created


def evaluate_all(db: Session, email_ready: bool) -> int:
    rules = db.scalars(select(AlertRule).where(AlertRule.active)).all()
    return sum(evaluate_rule(db, rule, email_ready) for rule in rules)


def _email(alert: Alert, rule: AlertRule, settings: Settings) -> tuple[str, str, str]:
    base = settings.frontend_url.rstrip("/")
    link = alert.link if not alert.link or alert.link.startswith("http") else f"{base}{alert.link}"
    subject = f"[Pinero] {alert.title}"
    text = f"{alert.title}\n\n{alert.body}\n\n{link or ''}\n\nRule: {rule.name}"
    body = (
        f"<p><strong>{html.escape(alert.title)}</strong></p><p>{html.escape(alert.body)}</p>"
        + (f'<p><a href="{html.escape(link)}">Open in Pinero</a></p>' if link else "")
        + f'<p style="color:#777;font-size:12px">Rule: {html.escape(rule.name)}. '
        "Values are as reported in their sources; see the linked page for provenance.</p>"
    )
    return subject, text, body


def deliver_pending(db: Session, sender: EmailSender | None, settings: Settings) -> dict[str, int]:
    outcome = {"sent": 0, "failed": 0}
    if sender is None:
        return outcome
    rows = db.execute(
        select(Alert, AlertRule, User)
        .join(AlertRule, AlertRule.id == Alert.rule_id)
        .join(User, User.id == Alert.owner_id)
        .where(Alert.email_status == "pending", User.is_active)
        .order_by(Alert.created_at, Alert.id)
        .limit(DELIVERY_BATCH)
    ).all()
    for alert, rule, user in rows:
        subject, text, body = _email(alert, rule, settings)
        try:
            sender.send(user.email, subject, text, body)
        except EmailError as error:
            alert.email_status = "failed"
            alert.email_error = str(error)[:300]
            outcome["failed"] += 1
        else:
            alert.email_status = "sent"
            alert.emailed_at = utcnow()
            outcome["sent"] += 1
        db.commit()
    return outcome


def unread_count(db: Session, owner_id: Any) -> int:
    return (
        db.scalar(select(func.count()).where(Alert.owner_id == owner_id, Alert.read_at.is_(None)))
        or 0
    )


def company_name(db: Session, company_id: int | None) -> str | None:
    company = db.get(Company, company_id) if company_id else None
    return company.legal_name if company else None
