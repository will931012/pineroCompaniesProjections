"""Periodic jobs. Each (kind, period) is enqueued once, whichever worker gets there first."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Job
from app.jobs.queue import enqueue


@dataclass(frozen=True)
class Periodic:
    kind: str
    minutes: int


def schedule(settings: Settings) -> list[Periodic]:
    return [
        Periodic("refresh_filings", settings.filings_poll_minutes),
        Periodic("poll_news", settings.news_poll_minutes),
        Periodic("evaluate_alerts", settings.alerts_poll_minutes),
        Periodic("refresh_prices", 360),
        # Phase 6: macro and yields daily; prices hourly within Tiingo's budget; research
        # (features, factor scores, regimes, predictions) daily, which is a no-op until a new
        # month end has prices; models retrained monthly; 13F data sets checked weekly.
        Periodic("refresh_macro", 1440),
        Periodic("load_prices", 60),
        Periodic("load_bitcoin", 1440),
        Periodic("update_research", 1440),
        Periodic("build_universe", 43200),
        Periodic("train_models", 43200),
        Periodic("refresh_ownership", 10080),
    ]


def tick(db: Session, settings: Settings, now: datetime) -> list[str]:
    """Enqueue every periodic job whose current period has no job yet."""
    enqueued = []
    for spec in schedule(settings):
        period = int(now.timestamp() // (spec.minutes * 60))
        key = f"{spec.kind}:{period}"
        if db.scalar(select(exists().where(Job.dedupe_key == key))):
            continue
        if enqueue(db, spec.kind, dedupe_key=key, max_attempts=3) is not None:
            enqueued.append(key)
    return enqueued
