# Backend Task Inventory

Owner: C

Purpose: identify work that should move out of synchronous web requests and
into a job/worker flow. This is an inventory, not the refactor itself.

## Summary

The first worker/job effort should start with email follow-ups because the
business logic is clear, failures are easy to retry, and it creates a reusable
pattern for heavier jobs like Excel quality imports and AI recommendations.

Recommended order:

1. Survey start follow-up emails
2. Booking confirmation emails
3. Reward reminder bulk email
4. Excel quality import
5. Dashboard AI recommendations
6. Built-in response quality checks
7. AI fill / AI question generation

## Task Inventory

| Task | Current Location | Trigger | Why Async | Priority | First Move |
| --- | --- | --- | --- | --- | --- |
| Survey start follow-up email | `api/main.py` `_send_survey_start_followup_email`, `_send_due_survey_start_followups`, `_survey_start_followup_worker` | Response start schedules `start_followup_scheduled_at`; startup worker polls due rows | Uses email provider; current startup loop is unreliable on serverless and can duplicate work on multi-instance deploys | P0 | Replace startup loop with persisted `email_followup` jobs and a manual/cron runner |
| Booking confirmation emails | `api/main.py` `_send_booking_confirmation_emails`, `_send_booking_confirmation_emails_for_response` | Participant books interview slots | Sends two external emails in user flow; failures should retry without blocking booking | P1 | Enqueue `booking_confirmation` job after booking is saved |
| Admin bulk email | `api/main.py` `/admin/users/bulk-email` | Admin action | Loops through all users and sends external emails; can time out and partially fail | P1 | Admin endpoint creates one queued email job per recipient and returns queued count |
| Welcome email | `api/main.py` `_send_welcome_followup_email` | Registration / profile completion | Email failure should not affect auth/profile flow; should retry | P1 | Enqueue `welcome_email` job after user record commits |
| Verification/password code email | `api/main.py` `_send_verification_email`, `/auth/send-code`, `/password-reset`, `/verify/send-code` | User requests code | User-facing, so needs fast path; provider failures should be recorded and rate-limited | P2 | Keep synchronous until job runner supports high-priority immediate jobs |
| Support alert email | `api/main.py` `send_support_alert_email` | User sends support message | Alert delivery should retry; support message save should not depend on email provider | P2 | Enqueue `support_alert` after support message commits |
| Excel quality import | `api/main.py` `/api/surveys/{survey_id}/quality/import-excel` | Researcher uploads `.xlsx` / `.xlsm` | Parses workbook, processes up to 500 rows, runs anomaly scoring and LLM review; likely to time out | P0 | Create `quality_import_jobs`; endpoint stores upload metadata and returns job id |
| Built-in response quality check | `api/main.py` `/surveys/{survey_id}/submit`; `app/quality_engine.py` | Participant submits built-in survey | Runs scoring and can call LLM during submit; submit should not wait on quality review | P1 | Commit response first, enqueue `builtin_quality_check` job |
| Ensure built-in quality checks | `api/main.py` `/api/surveys/{survey_id}/quality-results`; `app/quality_engine.py` `ensure_builtin_quality_checks` | Researcher opens quality results | Read endpoint may perform scoring work; list pages should not mutate/process large backlogs | P1 | Move backlog scoring to scheduled `quality_backfill` job |
| Dashboard AI recommendations | `api/main.py` `/dashboard`, `/dashboard/mobile`; `app/ai_growth/prediction.py` `recommend_surveys_for_user` | Participant opens dashboard | Cache misses can call Claude and run many DB lookups; directly affects homepage latency | P0 | Dashboard reads cache/local sort; enqueue `dashboard_recommendations` refresh job |
| Prediction recompute | `app/ai_growth/routes.py` `/api/prediction/recompute` | Researcher forces recompute | Can run candidate search and Claude summarization; should report progress | P1 | Endpoint creates `prediction_recompute` job |
| Prediction summary/respondents | `app/ai_growth/routes.py` `/api/surveys/{survey_id}/prediction/summary`, `/respondents` | Publisher views AI-growth insights | Cache miss may run batch Claude predictions | P1 | Read cached snapshots; enqueue refresh when stale |
| AI fill | `api/main.py` `/api/ai-fill`, `/admin/api/ai-fill` | Researcher/admin asks AI to draft listing fields | External API call; small enough to keep sync for now, but should have timeout/fallback | P2 | Leave sync initially; add timeout and error metrics |
| AI generate questions | `api/main.py` `/api/ai-generate-questions` | Researcher asks AI to draft questions | External API call with larger output; can be slow | P2 | Later move to `question_generation` job if usage grows |
| Discovery search | `api/main.py` `/admin/discovery/find`; `app/discovery/discovery.py` | Admin searches channels | May call Anthropic/web search-style discovery; admin-only but can be slow | P3 | Keep sync unless admin usage grows |
| Trust data cleanup | `api/main.py` `_cleanup_expired_trust_data` startup call | App startup | Startup cleanup can slow cold starts and runs per instance | P2 | Move to scheduled maintenance job |

## First Job Pilot

Pilot: survey start follow-up emails.

Reason:

- It is already conceptually a delayed background task.
- Existing code stores scheduling fields on `responses`.
- It is risky in serverless/multi-instance deployments because the worker is
  started inside FastAPI startup.
- It teaches the team the job lifecycle before heavier Excel/AI work.

Suggested first implementation:

```text
app/jobs/
  models.py
  service.py
  runner.py

app/notifications/
  email_service.py
  followup_jobs.py
```

First generic job fields:

```text
id
job_type
status: pending | running | done | failed
payload_json
attempts
max_attempts
run_after
locked_at
last_error
created_at
updated_at
```

First job types:

```text
survey_start_followup_email
booking_confirmation_email
reward_setup_reminder_email
```

## Acceptance Criteria For C's First PR

- Add job model and migration.
- Add helper to enqueue a job idempotently.
- Add runner function that claims due pending jobs.
- Move only survey start follow-up sending into the runner.
- Keep existing email body and behavior unchanged.
- Remove or disable the FastAPI startup polling loop after the runner is wired.
- Add a small script or endpoint for local/manual execution.

## Pilot Implementation Notes

The first PR creates a generic `background_jobs` table and wires the existing
survey start follow-up email into it. The second phase reuses the same runner
for booking confirmation emails, replacing the previous FastAPI
`BackgroundTasks` path with persisted jobs that can retry provider failures.
The third phase moves admin bulk email into the same queue, so the admin can
write a custom subject/body, the request only schedules recipient jobs, and the
runner sends them through the configured email provider in bounded batches.
The fourth phase moves Excel quality imports into the queue: uploads are saved
under `private_uploads/quality_imports`, the API returns a job id immediately,
and the runner performs workbook parsing, anomaly scoring, LLM review, and DB
writes outside the web request.
The fifth phase keeps dashboard rendering off the Claude path: dashboard pages
read only cached respondent predictions, enqueue missing recommendation refresh
jobs, and let the runner refill `respondent_predictions` asynchronously.
The final phase moves trust telemetry retention cleanup out of FastAPI startup:
startup now only enqueues a daily `trust_data_cleanup` maintenance job, and the
runner deletes expired risk signal and participation decision rows.

Manual run:

```bash
python scripts/run_due_jobs.py --limit 25
```

Suggested deployment step:

- Run the Alembic migration so `background_jobs` exists.
- Add a scheduled command that runs `python scripts/run_due_jobs.py --limit 25`
  every minute or every few minutes.
- Keep `INSIGHTA_ENABLE_LEGACY_FOLLOWUP_WORKER` unset in normal deploys. Set it
  to `1` only as a temporary rollback path to re-enable the old FastAPI startup
  polling loop.

## Coordination Notes

- Coordinate with A before adding job tables or migration files.
- Coordinate with B before touching dashboard AI recommendation calls.
- Do not refactor unrelated auth, payment, or publisher flows in the first PR.
