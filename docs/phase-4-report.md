# Phase 4 Report — News, events, alerts

Completed 2026-09-30.

## Decisions taken with the user

- **News sources.** The user first chose Yahoo Finance. Two facts ruled it out: Yahoo's terms
  forbid scraping, and its feed host (`feeds.finance.yahoo.com/robots.txt`) disallows every
  automated agent. The user decided that data must only be gathered within each source's
  published rules, so news comes from the **GDELT Project** (open data, free with attribution,
  at most one request every 5 seconds) plus SEC 8-K filings. Only headlines, outlets, times and
  links are stored; article text is never fetched from publishers.
- **Job queue.** Postgres-backed instead of Redis, so no new infrastructure.
- **Alert delivery.** Email through **Resend**.

## Implemented

**Background worker (`services/api/app/worker.py`, `app/jobs`)**
- **Queue:** a `jobs` table used as a queue. Workers claim jobs with `FOR UPDATE SKIP LOCKED`;
  a partial unique index allows one queued or running job per dedupe key.
- **Retries:** failures retry with exponential backoff up to `max_attempts`, and a job whose
  worker died is reclaimed after 30 minutes.
- **Scheduler:** built into the worker; each periodic job is enqueued once per period, so
  several worker replicas never double-run anything. Periods:
  - filings every 30 minutes
  - news every hour
  - alert rules every 5 minutes
  - prices every 6 hours (only when a market-data provider is configured)
- **Tracked companies:** every watchlist entry plus every active alert rule's scope.
- **Commands:** `python -m app.worker` (runs until stopped) and `--once` (processes due jobs,
  then exits).

**News (`app/providers/gdelt.py`, `app/events`)**
- **GDELT client:** at most one request every 10 seconds per process (GDELT's stated limit is 5
  s, but bursts near it get HTTP 429), with a 20 s backoff on 429. Queries use the company's
  short name as a phrase, plus "TICKER stock" when the ticker is distinctive.
- **Title normalisation:** GDELT tokenises headlines ("( NASDAQ : AAPL ) … $1 , 999"), so the
  spacing is undone.
- **Storage:** items are stored once per URL, each with its fetch provenance.
- **Company linking records how each link was made:**
  - ticker in the headline ("(NASDAQ: AAPL)", "$AAPL", or a bare ticker of 3+ letters) → high
    confidence;
  - company short name in the headline → high confidence;
  - the index's full-text match only → low confidence, hidden by default.
- **Novelty and story clustering:** headlines are embedded with the local BGE model from
  Phase 3. A news event is a *repeat* when an earlier event of the same company within 72
  hours has cosine similarity ≥ 0.82; without a model, word overlap ≥ 0.5. Repeats join the
  first report's cluster, so coverage can be counted ("+2 more outlets") and collapsed.

**Events (`app/events/taxonomy.py`, classifier `2026.09-3`)**
- **8-K events:** each 8-K becomes an event typed by its items, using the item's legal meaning
  (2.02 earnings, 5.02 leadership, 1.01 material agreement, 4.02 restatement, …). When items
  compete, the most consequential one names the event.
- **News events:** typed by ordered headline keyword rules; the first match wins, and the
  matched words are kept as evidence.
- **Relabelling:** events are relabelled automatically when the classifier version changes.
- **Evidence, not a score.** Each event carries its source, 8-K items or matched words, link
  method and novelty, plus how many outlets covered the story. No sentiment or materiality
  number is invented.

**Earnings**
- An earnings 8-K (Item 2.02) now loads its **EX-99 press release** from the filing index page
  as a section of the filing. It is shown, searchable and embeddable.
- This closes Phase 3 limitation 4 (8-K exhibits not loaded).

**Alerts (`app/alerts`, `app/providers/email.py`)**
- **Rule types:**
  - new SEC filing (chosen forms)
  - earnings release
  - news event (types; first reports only by default)
  - insider trade (Form 4 codes, minimum value)
  - daily price move (minimum %)
- **Scope:** tickers and/or one of the user's watchlists.
- **No flooding:** rules fire only for things discovered after they were created (and at most 7
  days old), so loading history never floods an inbox.
- **Idempotent:** each alert's subject key is unique per rule, so re-evaluation is harmless.
- **Email outcome recorded per alert:** `sent`, `failed` (with the provider's message),
  `not_configured` or `disabled`.
- **Email sender:** Resend, via the `EmailSender` port. Its test sender only delivers to the
  Resend account's own address until a domain is verified.

**API:**
- `GET /companies/{t}/news`
- `POST /companies/{t}/news/refresh` (analyst)
- `GET /companies/{t}/events`
- `GET /companies/{t}/earnings`
- `GET /events/types`
- `GET /feed`
- `/alerts/rules` (CRUD)
- `POST /alerts/rules/{id}/evaluate`
- `GET /alerts`
- `POST /alerts/read`
- `GET /alerts/status`
- `POST /alerts/test-email`
- `GET|POST /admin/jobs`

Migration `0005_activity` adds `jobs`, `news_items`, `news_mentions`, `events`,
`alert_rules` and `alerts`.

**Web**
- **Company tabs:**
  - *News*: Events (8-K + first news reports, counts by type, evidence on hover) and
    Headlines (confidence filter, repeat coverage, GDELT attribution, refresh button).
  - *Earnings*: releases with press-release excerpts and links into the viewer.
- **News page:** the watchlist event feed.
- **Alerts page:** rule builder whose options follow the rule type; email and worker status;
  rules list (pause, check now, delete); triggered alerts with email outcomes.
- **Dashboard:** watchlist events panel.
- **Admin:** jobs panel.
- **Navigation:** unread-alerts badge.

## Verification (run locally on 2026-09-30)

| Check | Result |
|---|---|
| API tests (pytest, PostgreSQL 18 + pgvector) | 192 passed (111 unit, 81 integration) |
| API lint / format / types | ruff clean · ruff format clean · mypy clean (101 files) |
| Migrations | upgrade → downgrade base → upgrade OK; `alembic check`: no drift |
| Web tests (Vitest) | 46 passed |
| Web lint / typecheck / build | ESLint clean · `tsc` clean · `next build` OK (`/news`, `/alerts`) |
| `npm audit` | 0 vulnerabilities |
| Worker on live data | `refresh_filings` for AAPL, MSFT, NVDA, JPM, BRK-B built 322 8-K events in 64 s; `poll_news` stored 162 headlines |
| Browser end to end (headless Edge, desktop + 390px) | dashboard feed, News page, company News/Earnings tabs, alert rule via the form; no page errors, no overflow |

**Observed on real data**
- **8-K event mix:** 133 earnings, 99 other disclosures, 50 leadership changes, 32 governance.
- **Syndicated copies:** two copies of the same Berkshire article were one story (the repeat
  had similarity 1.00). Distinct stories about the same company scored 0.59–0.74.
- **Real headlines found two misfires, both fixed with regression tests:**
  - "…Final Quarter as Berkshire Hathaway **CEO**" was labelled a leadership change. The rule
    now requires a change verb.
  - "…next major platform **upgrade**" was labelled an analyst rating. An analyst upgrade now
    requires a rating.
- **Link quality:** most full-text-only GDELT matches are unrelated ("Wave Waggin Wednesday"),
  which is why they are low confidence and hidden by default. For Microsoft, 68 weak matches
  were hidden.
- **Earnings release:** Apple's Q3 FY2026 release loaded from its EX-99.1 ("Apple reports
  third quarter results… revenue of $109.4 billion…").

Not verified here:
- **Email delivery:** no Resend key locally. The sender is covered by transport-level tests,
  and delivery outcomes by integration tests.
- **Price-move alerts on live prices:** Tiingo is not configured.

## Deploying this phase (Railway)

Phases 2–4 are undeployed. Deploy the API and web as in the earlier reports, run
`alembic upgrade head` (applies `0003`–`0005`), then add the worker:

1. New Railway service **worker** from the same repository. Root directory `/services/api`,
   config path `/services/api/railway.worker.json` (start command `python -m app.worker`).
   Copy the API's variables (`DATABASE_URL`, `SEC_USER_AGENT`, …).
2. Set `RESEND_API_KEY` and `ALERTS_EMAIL_FROM` on **both** API and worker.
   `ALERTS_EMAIL_FROM` must be a sender on a domain verified in Resend; the default test
   sender only reaches the Resend account's own email.
3. The worker needs no public domain and no port. The API's per-process rate limiter is
   unaffected.

## Known limitations and technical debt

1. **GDELT is best-effort.** Its API rate-limits by IP, and in testing it answered 429 for some
   companies even at one request every 10 s. Failed companies are retried on the next hourly
   poll. A licensed news API would be steadier; the provider port allows adding one.
2. **No upcoming earnings dates.** Reported releases come from 8-Ks, but future dates need an
   earnings-calendar source, which is not among the allowed sources.
3. **Headline-only classification.** Rules are deterministic and explainable but see only the
   title; a commentary headline that names an event is labelled as that event.
4. **Link precision.** Name matching can link a headline that merely lists a company (for
   example an ETF article naming five mega-caps). It is labelled "title name" so users can
   judge.
5. **Price alerts need a market-data provider** (Tiingo), plus the `refresh_prices` job.
6. **SEC throttle is per process.** The worker and the API each throttle SEC requests
   separately, so running both can briefly exceed SEC's 10 requests/s. Moving all on-demand
   SEC loads into the queue would fix it.
7. **Alerts are email-only by design.** The app lists them too, but there is no push or SMS.
8. **Open items from earlier phases:** market cap after splits, bank line items, CSP, password
   reset/MFA, GitHub auto-deploy on Railway, and the README note for `AUTH_DISABLED`.
