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

A fourth service, `pineroCompaniesProjections`, is left over from the initial
dashboard setup. It has no root directory, so it builds the monorepo root and
fails on every push. It should be deleted.

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

Unset, and therefore unavailable: `TIINGO_API_KEY` with
`MARKET_DATA_PROVIDER=tiingo` (price history and last close), and the `OIDC_*`
variables (single sign-on). `AUTH_SECRET` is only read when OIDC is configured.

## Unfinished: GitHub auto-deploy does not work

Pushes trigger builds on every repo-linked service, and because no service has a
root directory set, each one builds the monorepo root and fails. Railway keeps the
last good deployment serving, so the site stays up while the dashboard shows `api`
and `web` as `FAILED`. Both services were deployed with `railway up` instead.

This cannot currently be fixed from the CLI (see below). In the dashboard, per
service → Settings:

- `api` — Root Directory `/services/api`, config path `/services/api/railway.json`
- `web` — Root Directory `/apps/web`, config path `/apps/web/railway.json`
- delete the `pineroCompaniesProjections` service

The config path must be set separately: Railway's config file does **not** follow
the root directory, so it needs an absolute repository path. Until it is set, the
`railway.json` files in this repository are inert, which is why migrations have to
be run by hand.

## Operations

```powershell
railway link -p proud-blessing

# Deploy a subdirectory without relying on the root-directory setting
railway up services/api --path-as-root --service api --ci
railway up apps/web    --path-as-root --service web --ci

# Migrations (run by hand until the config path above is set)
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
