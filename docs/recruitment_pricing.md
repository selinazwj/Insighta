# Recruitment pricing rollout

Phase one introduces additive recruitment fees, quotes, and Stripe funding. Phase two adds completion review deadlines and original-card settlement refunds. Wallets, coupons, invoices, and niche payment links remain later phases.

## Rules

- External and built-in surveys: 20%, minimum $5 per target participant. Existing self-guided UX / product-test and diary-study options use the external-survey rate.
- Remote interviews: 25%, minimum $10 per target participant.
- In-person studies: 25%, minimum $15 per target participant.
- Cash and gift card quotes include the full researcher-entered participant reward. Fees are added on top.
- Raffle and volunteer quotes have no per-person reward and charge the study type's minimum recruitment fee per target participant. Raffle prizes are funded separately by the researcher.
- Urgency applies to the combined reward and fee: flexible 0%, within a month 5%, within a week 10%.
- Multi-session studies use the full reward per participant for completing all required sessions. Session count is scheduling metadata, not a second pricing multiplier. This matches the single existing completion payout for a participant.
- Niche audiences remain manually quoted; there is no self-service niche checkout.

`app/pricing.py` is the only pricing implementation. Both calculators call `/api/calculate-price`; publication recalculates on the server and ignores any client total. Money is converted to integer cents, percentage recruitment fees round half up to cents per participant, and urgency rounds half up once on the order subtotal.

## Configuration and snapshots

Edit `app/config/pricing.json` to change rates, minimums, or urgency without changing application code. Optionally set `INSIGHTA_PRICING_CONFIG` to a server-side JSON path. The file is read for each new quote. Keep its `version` current when editing prices. No public write endpoint changes configuration.

Apply migration `202611140001` before deploying with startup bootstrap disabled:

```sh
python scripts/run_migrations.py
```

The nullable JSON `surveys.pricing_snapshot` records inputs, currency, version, reward and recruitment cents, rate, minimum and whether it applied, urgency basis points and amount, subtotal, and total. Existing listings are not backfilled or repriced. New submissions save the quote with the draft before opening Checkout; payment confirmation preserves it. Funded quoted listings cannot change their price or participant cap through edit/builder routes; create a new listing for a new order.

Stripe must be configured for researcher publication, including raffles and volunteers. Missing configuration leaves an unpaid draft and returns 503. Admin demo publication remains available without charging and is explicitly marked `admin_demo`. Stripe Checkout uses the snapshot total in cents and an idempotency key. Only a signed webhook confirming the expected session, amount, currency, and paid status publishes a quoted study. Visiting the success URL alone does not mark a study paid. Webhook retries do not reopen paid closed studies.

Pricing and Terms describe automatic seven-day approval for verified completions without quality or trust holds, and original-card refunds after closeout. Unresolved holds pause settlement for review. Wallet balances are not available yet.

## Verification

```sh
python -m unittest discover -s tests -p test_pricing.py
python -m unittest discover -s tests -p test_publish_flow.py
python scripts/seo_audit.py --strict
python scripts/smoke_test_seo.py
node --test tests/test_pricing.cjs
```

Tests use isolated SQLite databases and mocked Stripe calls; they do not charge a card. Verify Stripe test-mode Checkout and webhook delivery in a staging deployment before release.

## Isolated Render staging

`render.staging.yaml` creates a separate free web service and free PostgreSQL database in Oregon from `codex/new-recruitment-pricing`. It does not adopt production resources. Automatic deploys and Blueprint auto-sync should remain off. Free Postgres expires after 30 days; this is disposable test data only.

The staging entrypoint applies migrations, derives URLs from the service's `RENDER_EXTERNAL_URL`, accepts only a database named `insighta_pricing_staging...`, and refuses non-test Stripe keys. Staging pages return noindex HTTP headers and robots.txt disallows crawling. No production database, email, OAuth, identity, or payment credentials are copied.

Initially the three Stripe variables are empty, so pricing pages work but payment correctly remains unavailable. Configure `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` using Stripe test-mode keys in the test service's Render Environment tab. Add a test-mode webhook for `https://<test-host>/webhook/stripe`, listening for `checkout.session.completed`, `refund.created`, `refund.updated`, and `refund.failed`, and store its signing secret as `STRIPE_WEBHOOK_SECRET`. Deploy to activate those values, then verify a test Checkout, paid webhook, immutable quote, and failed-payment boundaries. Do not paste secrets into Git or chat.

The free service does not have a pre-deploy step; the staging-only start command initializes the empty schema and applies Alembic before opening the HTTP port. Production keeps its existing deployment entrypoint.

## Phase two settlement

Apply migration `202611140002` before running with automatic schema bootstrap disabled. It adds UTC completion deadlines, close reasons, the original funding PaymentIntent, response review timestamps, and `study_settlements`. Existing orders without price snapshots and admin demos are excluded from automatic settlement and review deadlines.

A completion receives one seven-day review window. A researcher can reject within that window. After it expires, the worker automatically approves verified completions that have no unresolved trust or quality hold. A held response is not refunded as an unused place: settlement waits for verification or human approval. Responses count once per participant and rewards never exceed the purchased participant cap. Already-paid rewards cannot be revoked. The same guards cover notification review, quality review, completion edits, and bulk approval.

Closing a study or passing its explicitly selected completion deadline closes recruitment. Dates entered in the publishing form end at 23:59:59 UTC. Pending completion reviews finish before the settlement freezes. Completions after close or deadline are refused. A frozen settlement preserves the original quote, funded response IDs, count, refund amount in cents, and close reason. Financial decisions cannot change after freezing; funded studies and their settlement records cannot be deleted or reopened. Create a new listing for another order.

For ordinary closeout, refund the unused participant rewards plus recruitment fees and their urgency surcharge. Each response is capped at the purchased count; extra completions produce no additional charge or funded reward. Researcher cancellation is a separate close action for live studies: recruitment fees and their urgency surcharge remain payable, and only unused rewards plus their urgency component are refundable. Participant no-shows can be marked on the study detail page, freeing their booking slots for replacements without creating another purchase.

`app/settlements.py` computes amounts using snapshot integer cents. The in-process worker runs every minute by default; set `INSIGHTA_ENABLE_SETTLEMENT_WORKER=0` when using an external runner instead. The existing authenticated `/internal/jobs/run-due` runner also executes settlement cycles. For free Render services, in-process work pauses when the service sleeps: use an always-on service or an external authenticated scheduler for timely unattended closeouts. No new scheduled infrastructure is created by this code change.

Refunds use the captured PaymentIntent. Phase-one orders that stored only a Checkout Session can retrieve and verify their original paid session; no current-price recalculation is involved. Each settlement has a fixed Stripe idempotency key and a durable metadata marker. Before a create/retry, query all refunds on that payment for the marker, which also handles unknown outcomes beyond Stripe's idempotency-retention window. Pending refunds are polled; signed refund webhooks re-fetch authoritative status to avoid out-of-order state regressions. Failed/canceled provider refunds require support reconciliation. Repeated provider/network failures back off and stop after five attempts; the owner can queue reconciliation again if no provider refund ID is known. Missing Stripe keys never produce a fake successful refund.

Researchers see the settlement count, refund amount, pending/confirmed/held status, and retry option in study details. The owner-only API is `GET /api/surveys/{id}/settlement`; the retry endpoint accepts no client amounts. Refund provider calls are covered with mocks; no real card is charged or refunded by the tests.

Validation:

```sh
python -m unittest discover -s tests -p test_settlements.py
python -m unittest discover -s tests -p test_publish_flow.py
python -m unittest discover -s tests -p test_booking_calendar.py
```
