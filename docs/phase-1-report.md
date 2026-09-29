# Phase 1 Report — Foundation

Completed 2026-09-29.

## Implemented

**Backend (`services/api`)**
- Modular domain packages: `auth`, `companies`, `market_data`, `workspace`, `admin`, `system`,
  `ingestion`, `analytics`, `providers`, `audit`, `core`, `db`.
- Password auth (argon2id), optional OIDC SSO, DB-backed sessions, CSRF, origin checks,
  RBAC (viewer/analyst/admin), lockout, Redis rate limiting with in-memory fallback, audit log.
- Provenance: every provider request stored in `provider_fetches` and referenced by the data it
  produced.
- SEC EDGAR adapter: official ticker directory sync (idempotent, ticker reassignment keeps
  history, delisted listings deactivated, partial-download guard) and lazy submissions-profile
  enrichment (SIC classification, incorporation, fiscal year end, HQ, former names).
- Market-data port + Tiingo end-of-day adapter; bars validated, persisted, cached with a
  single-adjustment-basis rule, stale fallback with warnings; deterministic daily change.
- Watchlists, system status, admin endpoints, CLI (`create-user`, `sync-sec-directory`,
  `export-openapi`).
- Structured JSON logs with request IDs and redaction, Prometheus `/metrics`, optional Sentry,
  liveness/readiness, security headers, production configuration guard.
- Migration `0002_platform_foundation`.

**Web (`apps/web`)**
- Same-origin API rewrite; `proxy.ts` redirect for signed-out visitors; login/register.
- Workspace shell with full navigation; planned modules state their phase and contents.
- Dashboard (directory stats, provider health, search, watchlists), company directory,
  company page (header, 12 tabs with phase labels, price chart with range/basis, identity,
  listings, sources, data availability, add to watchlist), admin console.
- TanStack Query, Zustand (display preferences), API types generated from OpenAPI.

## Verification (run locally on 2026-09-29)

| Check | Result |
|---|---|
| API tests (pytest, real PostgreSQL 18) | 79 passed (34 unit, 45 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (56 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 28 passed |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK |
| `npm audit` | 0 vulnerabilities |
| End-to-end through the web proxy | redirect when signed out, 401 for anonymous API, register → HttpOnly cookie, CSRF enforced, foreign Origin rejected, logout revokes, planned pages render, unknown module 404 |
| Browser rendering (headless Edge, desktop + 390px) | no console errors, no horizontal overflow |
| Production web bundle without dev dependencies | starts and serves |

Not verified here: Docker images and `docker compose up` (Docker is not installed on the
development machine; CI builds both production images), live SEC and Tiingo calls (need your
credentials; adapters are covered by transport-level tests).

## Credentials you need to provide

| Variable | Needed for | How to get it |
|---|---|---|
| `SEC_USER_AGENT` | Company directory sync and profiles | Free; format `"Your Name you@example.com"` (SEC policy) |
| `MARKET_DATA_PROVIDER=tiingo` + `TIINGO_API_KEY` | Price history and last close | Tiingo account → API token. Check the licence tier for your use |
| `OIDC_*` + `AUTH_SECRET` (optional) | Single sign-on | Any OIDC provider; redirect URI `{FRONTEND_URL}/api/v1/auth/oidc/callback` |
| `SENTRY_DSN` (optional) | Error tracking | Sentry project |

Without them the app runs and says exactly what is missing; it never substitutes sample data.

## Technical debt and follow-ups

1. **Delete superseded modules** `services/api/app/{api,security,schemas}/` and
   `app/market_data/provider.py`, then remove their excludes from `pyproject.toml`.
2. **Version control**: the root is not a git repository and `apps/web` contains a nested `.git`
   from create-next-app. Remove `apps/web/.git`, then `git init` at the root.
3. SEC profile refresh runs inline on first page view (≤10 s timeout). Move to a background
   worker (planned with the job queue in Phase 4) and schedule nightly directory syncs.
4. No Content-Security-Policy yet (needs nonce support for Next.js scripts).
5. Password reset, email verification, and MFA are not implemented; OIDC is the recommended
   path for production identity.
6. Country is only derived for U.S. headquarters (EDGAR codes are not ISO); sector is the SIC
   division, not GICS.
7. shadcn/ui was not adopted; the existing design system was kept. Revisit when data tables and
   forms grow in Phase 2.
8. Metrics route labels omit the `/api/v1` prefix; `/metrics` should be network-restricted in
   deployment.
9. Starlette and Authlib emit deprecation notices recommending `httpx2`; evaluate the switch.
10. Intraday bars, quotes, and corporate-action tables are designed but not built.
