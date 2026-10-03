"""Operational commands.

python -m app.cli create-user --email you@example.com --name "You" --role admin
python -m app.cli sync-sec-directory
python -m app.cli sync-fundamentals --tickers AAPL,MSFT   (or --all-active; also loads SEC profiles)
python -m app.cli sync-filings --tickers AAPL,MSFT [--forms 10-K,10-Q,8-K] [--limit 8]
python -m app.cli prepare-embeddings   (downloads the embedding model; run at image build)
python -m app.cli export-openapi ../../packages/types/openapi.json
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ApiError


def _create_user(args: argparse.Namespace) -> int:
    from app.audit.service import record_audit_event
    from app.auth.service import create_user
    from app.db.session import get_sessionmaker

    password = getpass.getpass("Password (min 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        try:
            user = create_user(
                db, email=args.email, display_name=args.name, password=password, role=args.role
            )
        except ApiError as error:
            print(f"{error.code}: {error.message}", file=sys.stderr)
            return 1
        record_audit_event(
            db,
            action="cli.create_user",
            outcome="success",
            resource_type="user",
            resource_id=str(user.id),
            details={"role": args.role},
        )
        db.commit()
        print(f"Created {user.role} {user.email} ({user.id})")
    return 0


def _sync_sec_directory(_: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker
    from app.ingestion.sec_directory import sync_sec_directory
    from app.providers.base import ProviderError
    from app.providers.sec_edgar import build_sec_client

    settings = get_settings()
    client = build_sec_client(settings.sec_user_agent, settings.sec_timeout_seconds)
    with get_sessionmaker()() as db:
        try:
            summary = sync_sec_directory(db, client)
        except ProviderError as error:
            print(f"{error.code}: {error.message}", file=sys.stderr)
            return 1
    print(json.dumps(summary.as_dict(), indent=2))
    return 0


def _sync_fundamentals(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.companies.service import resolve_security
    from app.db.models import Company, Security
    from app.db.session import get_sessionmaker
    from app.fundamentals.service import sync_company
    from app.providers.sec_edgar import build_sec_client

    settings = get_settings()
    client = build_sec_client(settings.sec_user_agent, settings.sec_timeout_seconds)
    if not client.configured:
        print("SEC_USER_AGENT is not set.", file=sys.stderr)
        return 1
    failures = 0
    with get_sessionmaker()() as db:
        if args.tickers:
            ids = [resolve_security(db, t.strip()).company_id for t in args.tickers.split(",")]
        else:
            ids = list(
                db.scalars(
                    select(Company.id)
                    .join(Security, Security.company_id == Company.id)
                    .where(Security.is_active, Company.cik.is_not(None))
                    .group_by(Company.id)
                    .order_by(Company.id)
                    .limit(args.limit)
                )
            )
        for number, company_id in enumerate(ids, start=1):
            company = db.get(Company, company_id)
            assert company is not None
            status, message = sync_company(db, company, client, settings, force=args.force)
            failures += status not in {"current", "not_available"}
            detail = f" ({message})" if message else ""
            print(f"[{number}/{len(ids)}] CIK {company.cik} {company.legal_name}: {status}{detail}")
    return 1 if failures and args.tickers else 0


def _sync_filings(args: argparse.Namespace) -> int:
    from app.companies.service import resolve_security
    from app.db.session import get_sessionmaker
    from app.filings.service import (
        INSIDER_FORMS,
        embed_missing,
        load_filing_history,
        load_pending,
        refresh_filing_index,
    )
    from app.providers.embeddings import get_embedding_provider
    from app.providers.sec_edgar import build_sec_client

    settings = get_settings()
    client = build_sec_client(settings.sec_user_agent, settings.sec_timeout_seconds)
    if not client.configured:
        print("SEC_USER_AGENT is not set.", file=sys.stderr)
        return 1
    embedder = get_embedding_provider(settings)
    forms = [f.strip().upper() for f in args.forms.split(",") if f.strip()]
    forms += [f"{f}/A" for f in forms]
    failures = 0
    with get_sessionmaker()() as db:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        for number, ticker in enumerate(tickers, start=1):
            company = resolve_security(db, ticker).company
            status, message = refresh_filing_index(db, company, client, settings, force=True)
            if status != "current":
                failures += 1
                print(f"[{number}/{len(tickers)}] {ticker}: index {status} ({message})")
                continue
            if args.history:
                load_filing_history(db, company, client)
            documents = load_pending(db, company, client, forms, args.limit)
            insiders = load_pending(db, company, client, INSIDER_FORMS, args.insiders)
            embedded, remaining = (
                embed_missing(db, company.id, embedder, 1_000_000) if embedder else (0, 0)
            )
            failures += documents.get("failed", 0) + insiders.get("failed", 0) + (remaining > 0)
            print(
                f"[{number}/{len(tickers)}] {ticker}: documents {documents} · Form 4 {insiders}"
                f" · embedded {embedded} passages"
            )
    return 1 if failures else 0


def _prepare_embeddings(_: argparse.Namespace) -> int:
    from app.providers.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    if provider is None:
        print("EMBEDDING_PROVIDER is none; nothing to prepare.")
        return 0
    vector = provider.embed_query("warm-up")
    print(f"{provider.name} ready ({len(vector)} dimensions)")
    return 0 if len(vector) == provider.dimensions else 1


def _export_openapi(args: argparse.Namespace) -> int:
    from app.main import app

    target = Path(args.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target}")
    return 0


QUANT_STEPS = (
    "macro", "universe", "prices", "bitcoin", "research", "train", "ownership",
)  # fmt: skip


def _quant(args: argparse.Namespace) -> int:
    """Run one Phase 6 step through the same job handler the worker uses."""
    from app.db.session import get_sessionmaker
    from app.jobs.handlers import HANDLERS
    from app.worker import build_context

    kinds = {
        "macro": "refresh_macro", "universe": "build_universe", "prices": "load_prices",
        "bitcoin": "load_bitcoin", "research": "update_research", "train": "train_models",
        "ownership": "refresh_ownership",
    }  # fmt: skip
    payload: dict[str, object] = {}
    if args.sync_limit is not None:
        payload["sync_limit"] = args.sync_limit
    if args.workers > 1:
        payload["workers"] = args.workers
    context = build_context()
    with get_sessionmaker()() as db:
        result = HANDLERS[kinds[args.step]](db, context, payload)
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-user", help="Create a password-login user.")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=["viewer", "analyst", "admin"], default="analyst")
    create.set_defaults(handler=_create_user)

    sync = commands.add_parser("sync-sec-directory", help="Load SEC's official ticker directory.")
    sync.set_defaults(handler=_sync_sec_directory)

    fundamentals = commands.add_parser(
        "sync-fundamentals",
        help="Load SEC profiles and XBRL financial data, then recompute metrics.",
    )
    scope = fundamentals.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tickers", help="Comma-separated tickers, e.g. AAPL,MSFT")
    scope.add_argument("--all-active", action="store_true", help="Every active listed company")
    fundamentals.add_argument("--limit", type=int, default=100_000)
    fundamentals.add_argument("--force", action="store_true", help="Ignore the refresh TTL")
    fundamentals.set_defaults(handler=_sync_fundamentals)

    filings = commands.add_parser(
        "sync-filings",
        help="Index SEC filings, extract and embed documents, and parse Form 4s.",
    )
    filings.add_argument("--tickers", required=True, help="Comma-separated tickers")
    filings.add_argument("--forms", default="10-K,10-Q,8-K", help="Forms to load documents for")
    filings.add_argument("--limit", type=int, default=8, help="Newest documents per company")
    filings.add_argument("--insiders", type=int, default=40, help="Newest Form 4s per company")
    filings.add_argument("--history", action="store_true", help="Also index older filings")
    filings.set_defaults(handler=_sync_filings)

    prepare = commands.add_parser(
        "prepare-embeddings", help="Download and verify the embedding model."
    )
    prepare.set_defaults(handler=_prepare_embeddings)

    quant = commands.add_parser(
        "quant", help="Run one quantitative-research step (Phase 6) now, outside the worker."
    )
    quant.add_argument("step", choices=QUANT_STEPS)
    quant.add_argument("--sync-limit", type=int, help="universe: load at most N companies' filings")
    quant.add_argument("--workers", type=int, default=1, help="research: processes for features")
    quant.set_defaults(handler=_quant)

    export = commands.add_parser("export-openapi", help="Write the OpenAPI contract to a file.")
    export.add_argument("path")
    export.set_defaults(handler=_export_openapi)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
