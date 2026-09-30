import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.alerts.schemas import (
    AlertOut,
    AlertsResponse,
    AlertStatus,
    EvaluateOut,
    JobRunOut,
    ReadIn,
    RuleIn,
    RuleOut,
    RuleUpdate,
)
from app.alerts.service import deliver_pending, evaluate_rule, rule_company_ids
from app.audit.service import record_audit_event
from app.auth.dependencies import AppSettings, CurrentUser, DbSession, client_ip
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import Alert, AlertRule, Job, Security, User, Watchlist
from app.providers.email import EmailError, get_email_sender

router = APIRouter(prefix="/alerts", tags=["alerts"])

MAX_RULES = 50
WATCHED_JOBS = ("evaluate_alerts", "refresh_filings", "poll_news")


def _rule(db: Session, user: User, rule_id: uuid.UUID) -> AlertRule:
    rule = db.get(AlertRule, rule_id)
    if rule is None or rule.owner_id != user.id:
        raise ApiError(404, "alert_rule_not_found", "No such alert rule.")
    return rule


def _check_scope(
    db: Session, user: User, tickers: list[str], watchlist_id: uuid.UUID | None
) -> None:
    known = set(
        db.scalars(select(Security.ticker).where(Security.ticker.in_(tickers), Security.is_active))
    )
    if unknown := [t for t in tickers if t not in known]:
        raise ApiError(422, "unknown_ticker", f"Not in the directory: {', '.join(unknown)}.")
    if watchlist_id is not None:
        watchlist = db.get(Watchlist, watchlist_id)
        if watchlist is None or watchlist.owner_id != user.id:
            raise ApiError(404, "watchlist_not_found", "No such watchlist.")


def _out(db: Session, rule: AlertRule) -> RuleOut:
    watchlist = db.get(Watchlist, rule.watchlist_id) if rule.watchlist_id else None
    return RuleOut(
        id=rule.id,
        name=rule.name,
        kind=rule.kind,  # type: ignore[arg-type]
        params=rule.params,
        tickers=rule.tickers,
        watchlist_id=rule.watchlist_id,
        watchlist_name=watchlist.name if watchlist else None,
        email=rule.email,
        active=rule.active,
        created_at=rule.created_at,
        evaluated_at=rule.evaluated_at,
        companies=len(rule_company_ids(db, rule)),
    )


@router.get("/rules", response_model=list[RuleOut])
def list_rules(user: CurrentUser, db: DbSession) -> list[RuleOut]:
    rules = db.scalars(
        select(AlertRule).where(AlertRule.owner_id == user.id).order_by(AlertRule.created_at)
    )
    return [_out(db, r) for r in rules]


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleIn, user: CurrentUser, db: DbSession, request: Request) -> RuleOut:
    count = db.scalar(select(func.count()).where(AlertRule.owner_id == user.id)) or 0
    if count >= MAX_RULES:
        raise ApiError(422, "too_many_rules", f"At most {MAX_RULES} alert rules per user.")
    _check_scope(db, user, body.tickers, body.watchlist_id)
    rule = AlertRule(
        owner_id=user.id,
        name=body.name.strip(),
        kind=body.kind,
        params=body.params.model_dump(exclude_none=True),
        tickers=body.tickers,
        watchlist_id=body.watchlist_id,
        email=body.email,
        active=body.active,
        created_at=utcnow(),
    )
    db.add(rule)
    db.flush()
    record_audit_event(
        db,
        action="alerts.rule_create",
        outcome="success",
        actor_user_id=user.id,
        ip_address=client_ip(request),
        details={"rule_id": str(rule.id), "kind": rule.kind},
    )
    db.commit()
    return _out(db, rule)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RuleUpdate, user: CurrentUser, db: DbSession) -> RuleOut:
    rule = _rule(db, user, rule_id)
    tickers = (
        list(dict.fromkeys(t.strip().upper() for t in body.tickers if t.strip()))
        if body.tickers is not None
        else rule.tickers
    )
    watchlist_id = (
        body.watchlist_id if "watchlist_id" in body.model_fields_set else rule.watchlist_id
    )
    if not tickers and watchlist_id is None:
        raise ApiError(422, "empty_scope", "Choose at least one ticker or a watchlist.")
    _check_scope(db, user, tickers, watchlist_id)
    rule.tickers, rule.watchlist_id = tickers, watchlist_id
    if body.name is not None:
        rule.name = body.name.strip()
    if body.params is not None:
        rule.params = body.params.model_dump(exclude_none=True)
    if body.email is not None:
        rule.email = body.email
    if body.active is not None:
        rule.active = body.active
    db.commit()
    return _out(db, rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    db.delete(_rule(db, user, rule_id))
    db.commit()


@router.post("/rules/{rule_id}/evaluate", response_model=EvaluateOut)
def evaluate_now(
    rule_id: uuid.UUID, user: CurrentUser, db: DbSession, settings: AppSettings
) -> EvaluateOut:
    """Run one rule against data already stored (the worker does this every few minutes)."""
    rule = _rule(db, user, rule_id)
    sender = get_email_sender(settings)
    created = evaluate_rule(db, rule, email_ready=sender is not None)
    delivered = deliver_pending(db, sender, settings)
    return EvaluateOut(created=created, **delivered)


@router.get("", response_model=AlertsResponse)
def list_alerts(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AlertsResponse:
    rows = db.execute(
        select(Alert, AlertRule.name)
        .join(AlertRule, AlertRule.id == Alert.rule_id)
        .where(Alert.owner_id == user.id)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(limit)
    ).all()
    unread = db.scalar(
        select(func.count()).where(Alert.owner_id == user.id, Alert.read_at.is_(None))
    )
    return AlertsResponse(
        alerts=[
            AlertOut(
                id=a.id,
                rule_id=a.rule_id,
                rule_name=name,
                title=a.title,
                body=a.body,
                link=a.link,
                occurred_at=a.occurred_at,
                created_at=a.created_at,
                email_status=a.email_status,  # type: ignore[arg-type]
                email_error=a.email_error,
                read=a.read_at is not None,
            )
            for a, name in rows
        ],
        unread=unread or 0,
    )


@router.post("/read", status_code=204)
def mark_read(body: ReadIn, user: CurrentUser, db: DbSession) -> None:
    query = update(Alert).where(Alert.owner_id == user.id, Alert.read_at.is_(None))
    if body.ids is not None:
        query = query.where(Alert.id.in_(body.ids))
    db.execute(query.values(read_at=utcnow()))
    db.commit()


@router.get("/status", response_model=AlertStatus)
def alert_status(user: CurrentUser, db: DbSession, settings: AppSettings) -> AlertStatus:
    sender = get_email_sender(settings)
    jobs = []
    for kind in WATCHED_JOBS:
        job = db.scalar(
            select(Job)
            .where(Job.kind == kind, Job.status.in_(["done", "failed"]))
            .order_by(Job.finished_at.desc().nulls_last())
            .limit(1)
        )
        jobs.append(
            JobRunOut(
                kind=kind,
                status=job.status if job else "never",
                finished_at=job.finished_at if job else None,
                result=job.result if job else None,
                last_error=job.last_error if job else None,
            )
        )
    return AlertStatus(
        email_configured=sender is not None,
        email_provider=sender.name if sender else None,
        email_from=settings.alerts_email_from,
        recipient=user.email,
        jobs=jobs,
    )


@router.post("/test-email", status_code=204)
def test_email(user: CurrentUser, settings: AppSettings) -> None:
    sender = get_email_sender(settings)
    if sender is None:
        raise ApiError(503, "email_not_configured", "Set RESEND_API_KEY to send alert emails.")
    try:
        sender.send(
            user.email,
            "[Pinero] Test alert",
            "Alert emails from Pinero reach this address.",
            "<p>Alert emails from Pinero reach this address.</p>",
        )
    except EmailError as error:
        raise ApiError(502, "email_failed", str(error)) from error
