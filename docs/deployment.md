# Deployment (Railway)

Deployed 2026-09-30. This file is the handover: it records how the running
deployment is wired, what is still unfinished, and the traps that cost time, so a
new machine (or a new session) needs nothing but the repository.

## What is running

**Public URL:** <https://web-production-7d265.up.railway.app>

Railway project `proud-blessing`, environment `production`, on the Hobby plan.

| Service | Source | Notes |
|---|---|---|
| `web` | `apps/web`, Dockerfile `prod` stage | Only service with a public domain. Port 3000. |
| `api` | `services/api`, Dockerfile `prod` stage | **No public domain** — private network only. Port 8000. |
| `Postgres` | Railway `postgres-ssl:18` image | Private network only. |

Request path: browser → `web` → Next.js rewrite of `/api/v1/*` → private network →
`api` → `postgres.railway.internal`. The browser never talks to the API directly,
so the session cookie stays first-party and no CORS is needed.

## Configuration

Set on `api`:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | `postgresql+psycopg://` + references to the `Postgres` service (see below) |
| `FRONTEND_URL` | the `web` public URL |
| `PORT` | `8000` |
| `RATE_LIMIT_BACKEND` | `memory` |
| `AUTH_ALLOW_REGISTRATION` | `false` |
| `SEC_USER_AGENT` | name + contact email, as the SEC requires |
| `LOG_FORMAT` | `json` |

Set on `web`: `PORT=3000`, `NEXT_TELEMETRY_DISABLED=1`, and `API_INTERNAL_URL`
pointing at `http://` + the `api` service's private domain + `:8000`.

`DATABASE_URL` is written out by hand, as the `postgresql+psycopg://` scheme
followed by Railway template references to the `Postgres` service's `PGUSER`,
`PGPASSWORD`, `RAILWAY_PRIVATE_DOMAIN` and `PGDATABASE`. It does not reference
Railway's own `DATABASE_URL`, because that value uses the plain `postgresql://`
scheme and this app requires the `psycopg` driver prefix.

`APP_ENV=production` has two visible consequences: `/docs` is disabled, and
session cookies are marked `secure`. Rate limiting is per-process, which is
correct only while `api` runs a single replica.

`MARKET_DATA_PROVIDER=tiingo` and `TIINGO_API_KEY` were set on `api` on 2026-10-02 (price
history and last close); a check from the running service fetched AAPL bars. Unset, and
therefore unavailable: the `OIDC_*` variables (single sign-on). `AUTH_SECRET` is only read when OIDC is configured.

## Service settings and GitHub deploys

Fixed in the dashboard on 2026-10-02. Each service builds its own directory and reads its
own config file:

- `api` — Root Directory `/services/api`, config path `/services/api/railway.json`
- `web` — Root Directory `/apps/web`, config path `/apps/web/railway.json`

The config path must be set separately: Railway's config file does **not** follow the root
directory, so it needs an absolute repository path. With it set, `api` runs
`alembic upgrade head` as its pre-deploy command, so migrations apply on every deploy. The
leftover `pineroCompaniesProjections` service, which built the monorepo root and failed on
every push, was deleted.

A push to `main` therefore builds and deploys `api` and `web`. This had not yet been
exercised by a push when this was written. Check the first one in the dashboard.

Do not give `web` the `api` settings. On 2026-10-02 that briefly made `web` build the API
image, and the public site returned 502 until it was corrected.

## Not yet deployed: Phases 2–7 and the worker

Phases 2–7 add migrations `0003`–`0008` (applied by the pre-deploy command) and a background
**worker**: filings every 30 min, news every hour, alert rules every 5 min, and from Phase 6
macro data and Bitcoin daily, research prices hourly within Tiingo's budget, features and
predictions daily, the universe and models monthly, and 13F data sets weekly. When deploying:

- Create a service `worker` from this repository: Root Directory `/services/api`, config path
  `/services/api/railway.worker.json` (start command `python -m app.worker`, no public domain,
  no port). Give it the same variables as `api` (at least `DATABASE_URL`, `SEC_USER_AGENT`).
- For alert emails set `RESEND_API_KEY` and `ALERTS_EMAIL_FROM` on both `api` and `worker`.
  The sender must be on a domain verified in Resend; the default `onboarding@resend.dev` only
  reaches the Resend account owner.
- The API image now includes the embedding model (about 250 MB larger); the model loads into
  memory (150–250 MB) only when passages or headlines are embedded.
- Phase 6 needs `MARKET_DATA_PROVIDER=tiingo`, `TIINGO_API_KEY` and `FRED_API_KEY` on both
  `api` and `worker` (set on `api` on 2026-10-02); `OPENFIGI_API_KEY` is optional. The first
  research build is long: about 300 price histories at Tiingo's 50 requests an hour (about 7
  hours), then features and training. To speed it up, run the steps once by hand:
  `railway ssh --service worker python -m app.cli quant universe`, then `... quant prices`
  (repeat hourly, or let the worker do it), `... quant macro`, `... quant ownership`,
  `... quant research --workers 2`, `... quant train`.
- Tiingo's free plan is licensed for internal use only: do not share the deployed site's
  price-derived pages with other people without a commercial Tiingo plan.
- The image now installs `libgomp1` (needed by LightGBM) and includes scikit-learn and
  LightGBM (about 150 MB more).

## Operations

```powershell
railway link -p proud-blessing

# Deploy a subdirectory without relying on the root-directory setting
railway up services/api --path-as-root --service api --ci
railway up apps/web    --path-as-root --service web --ci

# Migrations (run automatically before each api deploy; by hand if needed)
railway ssh --service api alembic upgrade head
railway ssh --service api alembic current

# Load SEC's official ticker directory
railway ssh --service api python -m app.cli sync-sec-directory

# Logs
railway logs --service api --deployment --lines 50
railway logs --service web --build --lines 80 <deployment-id>
```

### Creating a user

`create-user` reads the password with `getpass`, so it needs a real terminal.
Piping into `railway ssh` hangs; use an interactive session in two steps:

```powershell
railway ssh --service api
python -m app.cli create-user --email you@example.com --name "Your Name" --role admin
```

The password must be at least 12 characters. If the interactive shell is
unavailable, generate the hash locally and insert the row directly — `create_user`
only inserts into `users`, so nothing is skipped except an audit record:

```powershell
cd services/api
.venv/Scripts/python -c "import getpass;from app.auth.passwords import hash_password;print(hash_password(getpass.getpass('Password: ')))"
```

```sql
INSERT INTO users (id, email, display_name, password_hash, role, is_active, failed_login_count)
VALUES (gen_random_uuid(), 'you@example.com', 'Your Name', '<hash>', 'admin', true, 0);
```

`created_at` and `updated_at` have server defaults. `email` must be lowercase and
`role` must be one of `viewer`, `analyst`, `admin`; both are check constraints.

## Traps worth remembering

**Railway CLI.** Version 4.16.1 cannot set a service's root directory, rename a
service, or delete one. Version 5.63.1 documents
`railway environment edit --service-config <svc> <path> <value>`, but it silently
returns `{"committed":false,"message":"No changes to apply"}` for every path
tried, by service name and by ID, plain and JSON-quoted. Use
`npx @railway/cli@5.63.1` for newer subcommands rather than upgrading the global
install. `railway login` refuses to run in a non-interactive shell.

`railway ssh --service <svc> <command>` works, but the remote side re-splits the
command through `sh`, so parentheses break it — an inline `python -c` fails.

**The API must bind `::`.** Railway's private network is IPv6. The production
Dockerfile binds `::` and honours `$PORT`; a dual-stack socket still accepts
IPv4, so Compose is unaffected. Binding `0.0.0.0` makes the API unreachable from
`web` with no obvious error.

**`API_INTERNAL_URL` is a build-time value.** Next.js resolves `rewrites()` during
`next build` and writes the destination into `.next/routes-manifest.json`; setting
the variable only at runtime has no effect. It works on Railway because the web
Dockerfile declares `ARG API_INTERNAL_URL` in the stage that runs `npm run build`,
and Railway injects service variables into builds for declared `ARG`s.

**`package-lock.json` completeness.** `npm ci` failed in the Docker build with
`Missing: @emnapi/runtime@1.11.3 from lock file`. `@img/sharp-wasm32` depends on
it, but the lock had no entry for it or `@emnapi/core`. npm 11.6.2 on Windows
tolerates the gap; npm 10.9.9 and 11.19.0 both reject it. Regenerating the lock on
Windows does not help, because npm prunes optional packages it cannot install on
the host platform. Both are now declared as `devDependencies` so they are in the
lock unconditionally. Check changes with `npx npm@10.9.9 ci --dry-run`, which
reproduces the container's stricter behaviour.

## Local development on Windows

Docker is not installed on the original machine, so the non-Docker path in the
README was used, with two obstacles.

**Smart App Control** is enabled and blocks low-reputation `.pyd` files with
"An Application Control policy has blocked this file". It blocked SQLAlchemy's
Cython extensions, `psycopg_binary._psycopg`, and
`cryptography.hazmat.bindings._rust`. Disabling it is a one-way switch, so instead:

- SQLAlchemy — move the `*_cy.*.pyd` files out of
  `services/api/.venv/Lib/site-packages/sqlalchemy/`; the `*_cy.py` pure-Python
  siblings then load. This is a configuration SQLAlchemy supports. Re-running
  `pip install` restores the `.pyd` files and the failure.
- psycopg — run with `PSYCOPG_IMPL=python` and
  `C:\Program Files\PostgreSQL\18\bin` on `PATH` so the ctypes backend finds
  `libpq.dll`.

`cryptography._rust` stays blocked but is not on the startup import path; it is
only reached by Authlib for OIDC, so password login works and OIDC would not.

Also set `RATE_LIMIT_BACKEND=memory` locally, since there is no Redis.

The local database was never set up: PostgreSQL 18 requires password
authentication, and the app's default `pinero` role does not exist on that server.
