# Observability

The app logs one structured line for every HTTP request from `api/main.py`.

## Request Timing

Each request log includes:

- HTTP method
- path
- response status
- duration in milliseconds
- request id from `X-Request-Id` or Vercel's `X-Vercel-Id`, when present

Requests slower than the configured threshold are logged as warnings.

## Environment Variables

```bash
INSIGHTA_LOG_LEVEL=INFO
INSIGHTA_SLOW_REQUEST_MS=1000
```

`INSIGHTA_LOG_LEVEL` defaults to `INFO`.

`INSIGHTA_SLOW_REQUEST_MS` defaults to `1000`. Set it lower while tuning
dashboard or admin pages, for example:

```bash
INSIGHTA_SLOW_REQUEST_MS=500
```

## Dashboard Guardrail

Participant dashboards push the SQL-safe targeting predicates into the database
and only inspect a bounded candidate window. They do not materialize all
published surveys in Python. Legacy age/tag compatibility checks remain in
Python, but both the returned candidate count and the number of SQL-prefiltered
rows inspected are capped:

```bash
INSIGHTA_DASHBOARD_CANDIDATE_LIMIT=100
INSIGHTA_DASHBOARD_SCAN_LIMIT=500
```

`INSIGHTA_DASHBOARD_CANDIDATE_LIMIT` caps cards that proceed to ranking/rendering.
`INSIGHTA_DASHBOARD_SCAN_LIMIT` prevents sparse legacy age/tag matches from
walking the entire published table page by page. Setting the candidate limit to
`0` removes only the output-card cap; setting the scan limit to `0` removes the
scan ceiling. Use either unbounded mode only for explicit diagnostics.

AI recommendation ranking remains enabled in production:

```bash
INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS=1
```

The request path never calls Claude synchronously. It performs one bulk cache
read. Cached Claude scores participate in ranking immediately; surveys without
a cache entry use the existing local field-fit + publish-time ordering for that
request. Missing survey IDs are deduplicated into a
`dashboard_recommendations_refresh` background job, which fills the AI cache for
subsequent requests. `INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS=0` remains only as an
emergency kill switch, not the normal deployment setting.

Each successful participant dashboard request emits a compact build log:

```text
dashboard_build path=/dashboard candidates=100 cache_hits=82 ai_enabled=True duration_ms=...
dashboard_build path=/dashboard/mobile candidates=100 visible=96 cache_hits=82 ai_enabled=True duration_ms=...
```

The request middleware additionally emits `slow_request ... path=/dashboard` or
`path=/dashboard/mobile` when the full request exceeds
`INSIGHTA_SLOW_REQUEST_MS`.

## Dashboard Benchmark

Run the local 10 / 100 / 1000 published-survey benchmark with AI enabled and a cold cache:

```bash
python scripts/benchmark_dashboard.py
```

The script reports p50/p95 request time and SQL statement counts for both
desktop and mobile dashboards. It deliberately does not execute the background
worker, so any Claude call on the synchronous request path would be a regression.
It uses a temporary SQLite database and does not touch the configured
development/production database.
