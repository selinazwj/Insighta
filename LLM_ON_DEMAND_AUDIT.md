# Insighta LLM On-Demand Audit

The project now defaults to **on-demand LLM usage**. Normal page loads, participant dashboard cache misses, response submission, and ordinary Excel quality imports do not make Anthropic API requests.

## Token-safe defaults

- `INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=0`: dashboard cache misses do not enqueue Claude work.
- Existing cached recommendation scores can still be read because cache reads spend no tokens.
- Legacy passive `dashboard_recommendations_refresh` jobs are completed without calling Claude while auto-refresh is disabled.
- Built-in response quality scoring uses local rules/anomaly detection by default (`run_llm=False`).
- Excel quality imports use local scoring by default. AI semantic review runs only when the researcher checks **Use AI semantic review for this import (uses LLM tokens)**.
- Publisher forecast cards no longer fetch AI forecasts on page load or when a previously selected AI tab is restored. Their read endpoints are cache-only.
- Anthropic is lazy-imported in AI Growth and Discovery; normal app startup does not load the SDK client.
- Screening draft generation defaults to deterministic local output at the function level; the explicit **Generate draft** endpoint opts into AI.

## Remaining LLM triggers (intentional)

These paths still call Anthropic because they correspond to an explicit AI action:

- Publisher **Generate forecast**, **Recompute**, and **Top candidates** actions. These create bounded background jobs; ordinary prediction GET requests never call Claude.
- The legacy explicit prediction preview/recompute POST endpoints (not used by the publisher UI).
- Built-in Results **AI review** and **AI review flagged** actions. These run in bounded background batches.
- Survey builder **Generate with AI**.
- Publish form **Fill with AI**.
- Survey results **Analyze Results**.
- Screening **Generate draft**.
- Admin channel discovery search.
- Excel quality import only when **Use AI semantic review** is checked.
- Optional passive dashboard refresh only if an operator explicitly sets `INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=1`.

## Deployment setting

Keep this value in production for token conservation:

```bash
INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=0
```

The existing five-minute cron can remain enabled for email, import, and maintenance jobs; with the default above it does not create or execute passive dashboard LLM refreshes.

## Shared usage controls

Insighta currently applies the same AI rules to every researcher; there are no
plan or spending tiers. Explicit prediction and built-in quality-review actions
are queued as idempotent background jobs and audited in `user_events` with the
requested units, actual analyzed units, token counts, status, feature, survey,
and job ID. Configure the shared guardrails with:

```bash
AI_DAILY_USER_OPERATIONS=10
AI_DAILY_PLATFORM_UNITS=500
AI_MAX_UNITS_PER_OPERATION=120
AI_REQUEST_COOLDOWN_MINUTES=10
AI_QUALITY_BATCH_LIMIT=5
```

Quick, Standard, and Full prediction scopes analyze at most 20, 50, and 120
candidates respectively. Repeated clicks inside the cooldown reuse the same job.
