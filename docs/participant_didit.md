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

Didit sends signed status webhooks directly to the app; receiving a verification
result does not require a periodic Vercel cron. Vercel Cron is disabled.
Fallback reconcile jobs wait until the background-job runner is invoked manually
from the admin panel or CLI. A registered pending session becomes eligible for
a check after five minutes, and a browser return brings that eligibility forward;
neither action starts a periodic scheduler. When invoked, the runner also picks
up pending sessions older than ten minutes. Completed reconcile jobs do not
permanently suppress subsequent checks. `CRON_SECRET` protects the optional
`/internal/jobs/run-due` invocation endpoint.
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

## Researcher listing requirements

In the listing wizard's **Verification requirements** section, **Verification
level** now offers Basic account check, Account and professional check, and
**ID check (Didit)**. The control is shared by survey/UX/diary publishing,
interview/in-person publishing, and listing editing; edits preselect the saved
requirement. Existing listings keep their current requirement until changed.

The existing `required_verification_tier` text column stores `didit` for the new
requirement; this needs no schema migration. Only active, verified evidence with
`method="didit"` satisfies that requirement. Email, Google, LinkedIn,
institutional email, self-reported data, expired evidence, and a cached user tier
cannot substitute for it. Active Didit evidence still satisfies existing basic
and trusted requirements. The retired `tier_1` / legacy ID-upload flow remains
unsupported. Identity is separate from any required email domain and screener.
Participants who need it are directed to `/verify?needs_tier=didit` before
starting a study. That page requests an ID check even if an email-only user adds
`done=1` to its URL.
