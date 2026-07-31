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

Participant dashboards cap the number of matched survey candidates sent into
ranking and rendering:

```bash
INSIGHTA_DASHBOARD_CANDIDATE_LIMIT=100
```

Set this to `0` to disable the cap during diagnostics.

Synchronous AI recommendations can also be disabled if dashboard latency or
Claude availability becomes a production risk:

```bash
INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS=0
```

When disabled, dashboards still render and sort using local field fit plus
publish time.
