"""A PostgreSQL work queue.

Jobs are rows. A worker claims the oldest due job with FOR UPDATE SKIP LOCKED, so several
workers never take the same job, and a crashed worker's job becomes claimable again once its
lock is older than STALE_AFTER. Failures retry with exponential backoff up to max_attempts.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import Job

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=30)
MAX_BACKOFF = timedelta(hours=1)


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> int | None:
    """Add a job; returns its id, or None when an active job has the same dedupe key."""
    statement = (
        insert(Job)
        .values(
            kind=kind,
            payload=payload or {},
            status="queued",
            run_at=run_at or utcnow(),
            attempts=0,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=text("status IN ('queued', 'running')"),
        )
        .returning(Job.id)
    )
    job_id = db.scalar(statement)
    db.commit()
    return job_id


def claim(db: Session, worker: str) -> Job | None:
    """Take the oldest due job (or a stale running one) and mark it running."""
    now = utcnow()
    candidate = (
        select(Job.id)
        .where(
            (Job.status == "queued") & (Job.run_at <= now)
            | (Job.status == "running") & (Job.locked_at < now - STALE_AFTER)
        )
        .order_by(Job.run_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    job = db.scalar(
        update(Job)
        .where(Job.id == candidate)
        .values(status="running", locked_by=worker, locked_at=now, attempts=Job.attempts + 1)
        .returning(Job)
    )
    db.commit()
    return job


def complete(db: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = "done"
    job.result = result
    job.finished_at = utcnow()
    job.last_error = None
    db.commit()


def fail(db: Session, job: Job, error: str) -> None:
    """Retry later with exponential backoff, or give up after max_attempts."""
    job.last_error = error[:2000]
    if job.attempts >= job.max_attempts:
        job.status = "failed"
        job.finished_at = utcnow()
    else:
        job.status = "queued"
        job.run_at = utcnow() + min(timedelta(minutes=2 ** (job.attempts - 1)), MAX_BACKOFF)
        job.locked_by = None
        job.locked_at = None
    db.commit()
