# Participant identity verification with Didit

The `participant-didit` branch builds on Yichun's `baa462e` webhook integration.
The participant can start verification from Home, Profile, or `/verify`, including
mobile Home and Profile. Email/account checks remain separate from identity checks.

## Configuration

Set secrets in the deployment's environment, never in frontend JavaScript:

- `DIDIT_API_KEY`: the application's Didit API key.
- `DIDIT_WORKFLOW_ID`: the published identity-verification workflow UUID.
- `DIDIT_WEBHOOK_SECRET`: the destination's signing secret (not the API key).
- `VERIFICATION_EVIDENCE_SECRET`: a stable secret for evidence references.
- `BASE_URL`: the public HTTPS application origin. For local testing use an HTTPS
  tunnel origin reachable by Didit; the browser return must reach the same app.
- `DIDIT_WEBHOOK_PREVIOUS_SECRET`: optional previous secret during rotation.
- `DIDIT_VERIFICATION_HOSTS`: optional comma-separated custom hosted verification
  domains. `verify.didit.me` is always allowed; redirects require HTTPS.

In Didit Business Console configure the webhook destination as
`{BASE_URL}/webhook/didit`, subscribing to `status.updated` (and optionally
`data.updated`). The browser callback is a **different** URL:
`{BASE_URL}/verify/didit/return`. Add the application's domain to the workflow's
allowed callback domains if that restriction is configured.

Run `python scripts/run_migrations.py` against the intended database before
starting the updated app, including local databases already used with the
webhook branch. The migrations create `didit_sessions`, `didit_webhook_events`,
and add `didit_sessions.provider_event_at`. `create_all` does not add columns to
existing tables. The Alembic head is `202610310001`.

Keep the existing background-job runner scheduled (for example once per minute),
with `CRON_SECRET` protecting `/internal/jobs/run-due`. A registered pending session
gets a check after five minutes, and a browser return brings the check forward.
Pending sessions older than ten minutes are picked up by the existing runner.
Completed reconcile jobs do not permanently suppress subsequent checks.
See [background job operations](background_jobs_runbook.md).

## Flow and API contract

1. The signed-in participant clicks **Verify with Didit**. The browser sends
   `POST /api/verification/didit/session` with `Content-Type: application/json`
   and an empty JSON object. Form submissions and foreign-origin requests are
   rejected. The endpoint ignores client-supplied account IDs, session IDs,
   workflow IDs, and verification results.
2. The server calls Didit's `POST /v3/session/` with the configured workflow,
   `vendor_data = str(current_user.id)`, and the browser callback. Didit reuses
   an unfinished session for the same workflow and vendor data. The response's
   account, workflow, and hosted HTTPS URL are validated. The local session is
   bound to that account before returning `{success, session_id, status, url}`.
   Existing active Didit evidence returns `status: "verified", url: null`.
3. The browser navigates to the hosted URL. No API key or provider document data
   is sent to frontend code.
4. The signed webhook updates the local session and verification evidence.
   Duplicate events are acknowledged; older events cannot replace newer results.
   Pending/declined/expired checks remove that session's active identity evidence.
   Retry jobs retain only the minimal envelope, without decision/document payloads.
5. The browser returns to `/verify/didit/return`. Its `status` query parameter is
   ignored. A supplied `verificationSessionId` must belong to the current user.
6. The page calls `GET /api/verification/status?session_id=...`, polling at five
   second intervals for about a minute while pending. It then offers manual
   refresh and explains that the participant may leave and check later.

Status responses separate account `status` / `tier` / `methods` from
`identity_status`. The latter is `unverified`, `pending`, `verified`, `rejected`,
or `expired`. `didit.status` describes the requested/latest session; a latest
session can still be pending while another session has active identity evidence.
Only active Didit evidence yields an overall `identity_status: "verified"`.
Identity UI must not treat email-only `status: "verified"` as an identity check.

The existing researcher endpoint remains restricted to participants with responses
in that researcher's own studies: `/api/participants/{participant_id}/verification`.
Personal status, start, and result responses carry `Cache-Control: no-store`.

## Verification

Run provider-mocked tests against the fixture's disposable SQLite database:

```bash
python -m unittest test_didit_participant -q
DATABASE_URL=sqlite:////private/tmp/insighta_didit_helpers_test.db \
  ENVIRONMENT=development DIDIT_WORKFLOW_ID='' \
  python -m unittest test_didit_webhook test_verification_assurance -q
```

The participant test module intentionally uses the existing isolated webhook
fixture; run it in a separate Python process from unrelated integration suites.
Tests cover server-side binding, authentication, cross-origin rejection, retry
privacy, duplicate starts, signed approval, rejected/expired evidence, delayed
callbacks, replay timestamp binding, missed-webhook reconciliation, and page entry
points. Real-provider testing still requires configured Didit credentials and an
HTTPS origin: complete one approved, declined, and pending/in-review test session,
then verify the signed callback and database result. No real identity sessions are
created by the automated tests.

API references: [Create Session](https://docs.didit.me/sessions-api/create-session),
[Webhooks](https://docs.didit.me/integration/webhooks).
