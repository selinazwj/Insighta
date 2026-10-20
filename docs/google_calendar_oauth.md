# Google Calendar connection

Cloud project: `Insighta Calendar` (`sylvan-airship-512220-j2`).
Web client: `Insighta Calendar Web`.
Authorized callback: `https://insightaco.org/auth/google/calendar/callback`.

The Calendar API is enabled. The consent screen is External / Testing with
`insightacom@gmail.com` added as a test user. Homepage, privacy and terms links
and the `insightaco.org` authorized domain are configured. Keep this separate from the existing Google sign-in
client. Calendar authorization requests `openid`, `email`, and
`https://www.googleapis.com/auth/calendar.events.owned`.

## Deployment

1. Install `requirements.txt` (includes cryptography).
2. Copy the three `GOOGLE_CALENDAR_*` values from the local ignored `.env` to the
   server environment, using a secret manager or the hosting provider's encrypted
   environment settings. Never commit or print these values.
3. Set `BASE_URL=https://insightaco.org`. Keep `GOOGLE_CALENDAR_TOKEN_KEY` stable;
   changing it invalidates saved tokens and pending OAuth flows.
4. Run `python scripts/run_migrations.py` using the deployment's normal migration
   procedure. Revision `202610200001` creates the connection and event tables.
5. Deploy and confirm the existing background-job runner is active. It handles
   `google_calendar_sync` jobs alongside confirmation email jobs.
6. In Google Auth Platform, add test users while the app is in Testing. Complete
   Google's public-app verification before offering the connection broadly.
7. Open My Studies or Publisher Schedule, connect a test account, and verify its
   existing scheduled bookings appear in Google Calendar after the job runs.

## Behavior

Connecting queues existing scheduled bookings. New or changed native booking
selections queue sync for each connected participant/researcher. Event IDs are
stable across retries. Changing a selection updates events; surplus old session
events are removed. There is no inbound synchronization of edits made in Google
Calendar. External booking providers remain outside the native booking sync.

Slots without a recorded time zone remain available through the manual calendar
links; automatic sync refuses to guess their time zone. Failed sync jobs use the
existing retry/alert mechanism. Revoked authorization shows a reconnect action.
Disconnecting revokes the calendar token and preserves previously saved events.

Tokens are encrypted with Fernet. OAuth state expires after ten minutes, is bound
to the logged-in website user and a browser nonce, and uses PKCE. Calendar actions
use the project's existing account/session authentication.

## Verification

`venv/bin/python -m unittest discover -s tests -p test_booking_calendar.py`

Tests cover export permissions, time zones, website and email links, OAuth user
binding/cancellation, encrypted token storage, token refresh, backfill jobs,
idempotent sync, rescheduling, and disconnect CSRF protection. Google API requests
are mocked; an actual production OAuth round-trip still requires deployment.
