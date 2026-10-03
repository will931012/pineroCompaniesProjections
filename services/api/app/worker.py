"""Background worker: runs periodic and on-demand jobs from the PostgreSQL queue.

python -m app.worker            run until stopped (Railway: a second service, same image)
python -m app.worker --once     run the scheduler once, process due jobs, then exit
"""

import argparse
import logging
import os
import signal
import socket
import threading
import time
import traceback

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import utcnow
from app.db.session import get_sessionmaker
from app.jobs.handlers import HANDLERS, WorkerContext
from app.jobs.queue import claim, complete, fail
from app.jobs.scheduler import tick
from app.providers.email import get_email_sender
from app.providers.embeddings import get_embedding_provider
from app.providers.fred import build_fred_client
from app.providers.gdelt import build_gdelt_client
from app.providers.market_data.registry import get_market_data_provider
from app.providers.openfigi import build_openfigi_client
from app.providers.sec_edgar import build_sec_client
from app.providers.treasury import build_treasury_client

logger = logging.getLogger("app.worker")

IDLE_SECONDS = 5.0
TICK_SECONDS = 30.0


def build_context() -> WorkerContext:
    settings = get_settings()
    return WorkerContext(
        settings=settings,
        sec=build_sec_client(settings.sec_user_agent, settings.sec_timeout_seconds),
        gdelt=build_gdelt_client(settings.sec_user_agent),
        embedder=get_embedding_provider(settings),
        email=get_email_sender(settings),
        market=get_market_data_provider(),
        fred=build_fred_client(
            settings.fred_api_key.get_secret_value() if settings.fred_api_key else None
        ),
        figi=build_openfigi_client(
            settings.openfigi_api_key.get_secret_value() if settings.openfigi_api_key else None
        ),
        treasury=build_treasury_client(settings.sec_user_agent),
    )


def run_one(context: WorkerContext, worker_id: str) -> bool:
    """Claim and run one due job; returns False when there was none."""
    with get_sessionmaker()() as db:
        job = claim(db, worker_id)
        if job is None:
            return False
        handler = HANDLERS.get(job.kind)
        started = time.perf_counter()
        try:
            if handler is None:
                raise LookupError(f"No handler for job kind {job.kind!r}.")
            result = handler(db, context, job.payload)
        except Exception as exc:
            db.rollback()
            fail(db, job, "".join(traceback.format_exception_only(exc)).strip())
            logger.exception("job_failed", extra={"job_id": job.id, "kind": job.kind})
        else:
            complete(db, job, result)
            logger.info(
                "job_done",
                extra={
                    "job_id": job.id,
                    "kind": job.kind,
                    "ms": int((time.perf_counter() - started) * 1000),
                },
            )
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="Process due jobs, then exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    context = build_context()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("worker_started", extra={"worker": worker_id, "email": context.email is not None})

    last_tick = 0.0
    while not stop.is_set():
        if time.monotonic() - last_tick >= TICK_SECONDS:
            with get_sessionmaker()() as db:
                tick(db, settings, utcnow())
            last_tick = time.monotonic()
        worked = run_one(context, worker_id)
        if args.once and not worked:
            break
        if not worked:
            stop.wait(IDLE_SECONDS)
    logger.info("worker_stopped", extra={"worker": worker_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
