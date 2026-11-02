# Background Jobs Runbook

Owner: C

Purpose: deploy and verify the production background job runner for queued
emails, quality imports, optional dashboard recommendation refreshes, and
maintenance cleanup. Passive dashboard AI refresh is disabled by default to
keep LLM usage on-demand.

## LLM token-safety default

Keep `INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=0` in normal production if LLM usage must be on-demand only. Passive dashboard recommendation jobs are not created, and legacy passive recommendation jobs are skipped without calling Claude.

## Production Setup

1. Run `python scripts/run_migrations.py` (Render Pre-Deploy or manual) so the
   `background_jobs` table exists. See `docs/database_migrations.md`.
2. Set `CRON_SECRET` in the host env (Render / cron caller) to a long random value.
3. Set `BACKGROUND_JOB_ALERT_EMAIL` if final-failure alerts should go somewhere
   other than `SUPPORT_ALERT_EMAIL`.
4. Keep `INSIGHTA_ENABLE_LEGACY_FOLLOWUP_WORKER` unset during normal deploys.
5. Vercel Cron is disabled: `vercel.json` intentionally has no `crons` entry.
   Run due jobs manually from the admin panel when needed. Queued jobs wait
   until a runner is invoked; no replacement periodic scheduler is configured.

## Verification Checklist

1. Open `/admin` and sign in with `ADMIN_KEY`.
2. Open the `Jobs` sidebar item.
3. Confirm the stats row loads without an error.
4. Click `Run due jobs`.
5. Confirm the result message reports claimed/succeeded/failed counts.
6. Refresh the page and confirm recent jobs have updated statuses.
7. Queue a low-risk admin email or test import only when you are ready for the
   runner to process real jobs.

## Expected Behavior

- Vercel does not periodically invoke `/internal/jobs/run-due`.
- The cron endpoint requires `Authorization: Bearer <CRON_SECRET>`.
- Admins can manually run due jobs from `/admin#jobs`.
- Jobs stuck in `running` for more than 15 minutes are recovered before new work
  is claimed.
- Jobs that exhaust attempts move to `failed`.
- Final failures send an alert email to `BACKGROUND_JOB_ALERT_EMAIL` or
  `SUPPORT_ALERT_EMAIL`.

## Rollback

If the runner causes production trouble:

1. Stop any separately configured scheduler; Vercel Cron is already disabled.
2. Stop clicking `Run due jobs` in admin.
3. Set `INSIGHTA_ENABLE_LEGACY_FOLLOWUP_WORKER=1` only if survey follow-up
   emails need the temporary old startup worker path.
4. Inspect `/admin#jobs` for failed jobs and error summaries.
5. Resume manual runs after the failing handler is fixed. Configure a periodic
   scheduler only when its frequency and hosting plan have been agreed.

## Manual Local Run

```bash
python scripts/run_due_jobs.py --limit 25
```

Use a small limit first when testing against production-like data.
