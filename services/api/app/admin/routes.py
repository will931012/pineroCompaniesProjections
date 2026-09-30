import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.audit.service import record_audit_event
from app.auth.dependencies import AdminUser, DbSession, client_ip
from app.auth.schemas import Role
from app.auth.service import count_admins
from app.auth.sessions import revoke_all_sessions
from app.companies.routes import SecClient
from app.companies.service import resolve_security
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.models import AuditEvent, Job, ProviderFetch, User
from app.fundamentals.service import sync_company
from app.ingestion.sec_directory import sync_sec_directory
from app.jobs.queue import enqueue
from app.providers.base import ProviderError

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class AdminUserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class ProviderFetchOut(BaseModel):
    id: int
    provider: str
    dataset: str
    subject: str | None
    status: str
    http_status: int | None
    error_code: str | None
    error_message: str | None
    record_count: int | None
    rejected_count: int | None
    latency_ms: int
    retrieved_at: datetime


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    action: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    details: dict[str, Any]


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(_: AdminUser, db: DbSession) -> list[AdminUserOut]:
    return [_user_out(u) for u in db.scalars(select(User).order_by(User.created_at))]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: uuid.UUID, body: AdminUserUpdate, admin: AdminUser, db: DbSession, request: Request
) -> AdminUserOut:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "User not found.")
    removing_admin = user.role == "admin" and (
        (body.role is not None and body.role != "admin") or body.is_active is False
    )
    if removing_admin and count_admins(db) <= 1:
        raise ApiError(409, "last_admin", "At least one active administrator must remain.")
    changes: dict[str, Any] = {}
    if body.role is not None and body.role != user.role:
        changes["role"] = {"from": user.role, "to": body.role}
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = {"from": user.is_active, "to": body.is_active}
        user.is_active = body.is_active
    if changes:
        # Privilege changes take effect immediately by ending existing sessions.
        revoke_all_sessions(db, user)
        record_audit_event(
            db,
            action="admin.user_update",
            outcome="success",
            actor_user_id=admin.id,
            resource_type="user",
            resource_id=str(user.id),
            ip_address=client_ip(request),
            details=changes,
        )
    db.commit()
    return _user_out(user)


@router.post("/ingestion/sec-directory")
def trigger_sec_directory_sync(
    admin: AdminUser, db: DbSession, sec: SecClient, request: Request
) -> dict[str, int | bool]:
    try:
        summary = sync_sec_directory(db, sec)
    except ProviderError as error:
        record_audit_event(
            db,
            action="admin.sec_directory_sync",
            outcome="failure",
            actor_user_id=admin.id,
            ip_address=client_ip(request),
            details={"error_code": error.code},
        )
        db.commit()
        raise ApiError(503, error.code, error.message) from error
    record_audit_event(
        db,
        action="admin.sec_directory_sync",
        outcome="success",
        actor_user_id=admin.id,
        ip_address=client_ip(request),
        details=summary.as_dict(),
    )
    db.commit()
    return summary.as_dict()


class FundamentalsSyncIn(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=25)


class FundamentalsSyncResult(BaseModel):
    ticker: str
    status: str
    message: str | None


@router.post("/ingestion/fundamentals", response_model=list[FundamentalsSyncResult])
def trigger_fundamentals_sync(
    body: FundamentalsSyncIn, admin: AdminUser, db: DbSession, sec: SecClient, request: Request
) -> list[FundamentalsSyncResult]:
    results = []
    for ticker in dict.fromkeys(t.strip().upper() for t in body.tickers if t.strip()):
        company = resolve_security(db, ticker).company
        status, message = sync_company(db, company, sec, get_settings(), force=True)
        results.append(FundamentalsSyncResult(ticker=ticker, status=status, message=message))
    record_audit_event(
        db,
        action="admin.fundamentals_sync",
        outcome="success",
        actor_user_id=admin.id,
        ip_address=client_ip(request),
        details={r.ticker: r.status for r in results},
    )
    db.commit()
    return results


@router.get("/provider-fetches", response_model=list[ProviderFetchOut])
def provider_fetches(
    _: AdminUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ProviderFetchOut]:
    rows = db.scalars(select(ProviderFetch).order_by(ProviderFetch.id.desc()).limit(limit))
    return [ProviderFetchOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/audit-events", response_model=list[AuditEventOut])
def audit_events(
    _: AdminUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AuditEventOut]:
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit))
    return [AuditEventOut.model_validate(r, from_attributes=True) for r in rows]


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    run_at: datetime
    finished_at: datetime | None
    last_error: str | None
    result: dict[str, Any] | None


class JobsOverview(BaseModel):
    counts: dict[str, dict[str, int]]
    recent: list[JobOut]
    worker_seen: datetime | None


class JobIn(BaseModel):
    kind: str = Field(max_length=60)
    tickers: list[str] = Field(default_factory=list, max_length=25)


@router.get("/jobs", response_model=JobsOverview)
def jobs_overview(_: AdminUser, db: DbSession) -> JobsOverview:
    counts: dict[str, dict[str, int]] = {}
    for kind, status, count in db.execute(
        select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
    ):
        counts.setdefault(kind, {})[status] = count
    recent = db.scalars(select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(40))
    return JobsOverview(
        counts=counts,
        recent=[
            JobOut(
                id=j.id,
                kind=j.kind,
                status=j.status,
                attempts=j.attempts,
                run_at=j.run_at,
                finished_at=j.finished_at,
                last_error=j.last_error,
                result=j.result,
            )
            for j in recent
        ],
        worker_seen=db.scalar(select(func.max(Job.locked_at))),
    )


@router.post("/jobs", response_model=JobOut, status_code=201)
def enqueue_job(body: JobIn, admin: AdminUser, db: DbSession, request: Request) -> JobOut:
    """Queue a job now; the worker process runs it."""
    from app.jobs.handlers import HANDLERS

    if body.kind not in HANDLERS:
        raise ApiError(422, "unknown_job", f"Job kinds: {', '.join(sorted(HANDLERS))}.")
    payload: dict[str, Any] = {}
    if body.tickers:
        payload["company_ids"] = [resolve_security(db, t).company_id for t in body.tickers]
    job_id = enqueue(db, body.kind, payload)
    record_audit_event(
        db,
        action="admin.job_enqueue",
        outcome="success",
        actor_user_id=admin.id,
        ip_address=client_ip(request),
        details={"kind": body.kind, "tickers": body.tickers},
    )
    db.commit()
    job = db.get(Job, job_id)
    assert job is not None
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        run_at=job.run_at,
        finished_at=job.finished_at,
        last_error=job.last_error,
        result=job.result,
    )
