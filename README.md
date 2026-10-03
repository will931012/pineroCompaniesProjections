# Pinero Research

A web-first, source-first platform for public-company research. Every value it shows links to
where it came from; nothing is estimated, sampled, or filled in. Deterministic calculations,
model outputs, and AI interpretation are kept as separate systems.

**Status:** Phase 6 (Quantitative research) complete. See [docs/phase-6-report.md](docs/phase-6-report.md),
[docs/phase-5-report.md](docs/phase-5-report.md),
[docs/phase-4-report.md](docs/phase-4-report.md),
[docs/phase-3-report.md](docs/phase-3-report.md),
[docs/phase-2-report.md](docs/phase-2-report.md), [docs/phase-1-report.md](docs/phase-1-report.md),
[docs/architecture.md](docs/architecture.md), and [docs/roadmap.md](docs/roadmap.md).

**Deployed on Railway.** See [docs/deployment.md](docs/deployment.md) for the running
setup, the remaining manual steps, and the platform traps worth knowing.

## Run with Docker

Requires Docker Desktop.

```powershell
Copy-Item .env.example .env        # then set SEC_USER_AGENT (and TIINGO_API_KEY for prices)
docker compose up --build
docker compose exec api python -m app.cli create-user --email you@example.com --name "You" --role admin
docker compose exec api python -m app.cli sync-sec-directory
docker compose exec api python -m app.cli sync-fundamentals --tickers AAPL,MSFT,NVDA   # or --all-active
docker compose exec api python -m app.cli sync-filings --tickers AAPL,MSFT,NVDA        # filings, Form 4s, search index
# Phase 6 research data (needs TIINGO_API_KEY and FRED_API_KEY; prices load at Tiingo's 50/hour)
docker compose exec api python -m app.cli quant universe     # point-in-time model universe from SEC
docker compose exec api python -m app.cli quant macro        # FRED series and the Treasury curve
docker compose exec api python -m app.cli quant prices       # one hourly batch; the worker repeats it
docker compose exec api python -m app.cli quant research --workers 2   # features, factors, regimes
docker compose exec api python -m app.cli quant train        # walk-forward evaluation and models
```

The `worker` service runs background jobs (filings, news, alerts) for companies on watchlists
and in alert rules. Without Docker, run `python -m app.worker` next to the API.

Open <http://localhost:3000> and sign in. API docs: <http://localhost:8000/docs>.
If a port is taken, set `WEB_PORT`, `API_PORT`, `POSTGRES_PORT`, or `REDIS_PORT` in `.env`.

## Run without Docker

Requires Python 3.12+, Node.js 22+, and PostgreSQL 15+ (Redis optional; rate limiting falls back
to memory with `RATE_LIMIT_BACKEND=memory`).

```powershell
# API
cd services/api
python -m venv .venv; .venv\Scripts\python -m pip install -e ".[dev]"
$env:DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@localhost:5432/pinero"
$env:RATE_LIMIT_BACKEND = "memory"
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --port 8000

# Web (second terminal)
cd apps/web
npm ci
$env:API_INTERNAL_URL = "http://localhost:8000"
npm run dev
```

## Configuration

All settings come from environment variables; see [.env.example](.env.example). Without provider
credentials the app runs and states exactly what is missing:

| Variable | Enables |
|---|---|
| `SEC_USER_AGENT` | Company directory sync and SEC company profiles (free; must identify you) |
| `MARKET_DATA_PROVIDER=tiingo`, `TIINGO_API_KEY` | Daily price history and last close |
| `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `AUTH_SECRET` | Optional single sign-on |
| `RESEND_API_KEY`, `ALERTS_EMAIL_FROM` (API and worker) | Alert emails through Resend; the from-address must be on a verified domain |

## Checks

```powershell
# API (integration tests need a disposable database; it is truncated between tests)
cd services/api
$env:TEST_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@localhost:5432/pinero_test"
.venv\Scripts\python -m ruff check .; .venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy
.venv\Scripts\python -m pytest

# Web
cd apps/web
npm run lint; npm run typecheck; npm test; npm run build
```

After changing API schemas, regenerate the contract and client types:

```powershell
cd services/api; .venv\Scripts\python -m app.cli export-openapi ../../packages/types/openapi.json
cd ../../apps/web; npm run gen:api
```

## Scope and safety

Research and simulation only. There is no real-money trading, and broker integrations are a
future, explicitly gated module. Data is used only through official or licensed interfaces.
