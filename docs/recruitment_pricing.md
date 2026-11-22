# Recruitment pricing rollout

Phase one introduces additive recruitment fees, quotes, and Stripe funding. Phase two adds completion review deadlines and original-card settlement refunds. Phase three adds researcher wallets, promotional recruitment credits, and billing exports. Phase four adds manual niche requests, versioned offers, and owner-bound payment pages.

## Rules

**Platform minimum recruitment fee per participant:** the minimum is paid to Insighta for recruitment and service delivery. It is added to the researcher-entered participant reward. Participants receive that reward in full. For example, a $40 participant reward plus a $15 platform minimum fee means $55 per person before urgency; $40 goes to the participant and $15 is the platform recruitment fee.

- External and built-in surveys: 20%, with a $5 minimum platform recruitment fee per target participant. Existing self-guided UX / product-test and diary-study options use the external-survey rate.
- Remote interviews: 25%, with a $10 minimum platform recruitment fee per target participant.
- In-person studies: 25%, with a $15 minimum platform recruitment fee per target participant.
- Cash and gift card quotes include the full researcher-entered participant reward. Fees are added on top.
- Raffle and volunteer quotes have no per-person reward and charge the study type's minimum recruitment fee per target participant. Raffle prizes are funded separately by the researcher.
- Urgency applies to the combined reward and fee: flexible 0%, within a month 5%, within a week 10%.
- Multi-session studies use the full reward per participant for completing all required sessions. Session count is scheduling metadata, not a second pricing multiplier. This matches the single existing completion payout for a participant.
- Niche audiences use a manual quote. Standard-audience estimates do not authorize niche publication or payment.

`app/pricing.py` is the only pricing implementation. Both calculators call `/api/calculate-price`; publication recalculates on the server and ignores any client total. Money is converted to integer cents, percentage recruitment fees round half up to cents per participant, and urgency rounds half up once on the order subtotal.

## Configuration and snapshots

Edit `app/config/pricing.json` to change rates, minimums, or urgency without changing application code. Optionally set `INSIGHTA_PRICING_CONFIG` to a server-side JSON path. The file is read for each new quote. Keep its `version` current when editing prices. No public write endpoint changes configuration.

Apply migration `202611140001` before deploying with startup bootstrap disabled:

```sh
python scripts/run_migrations.py
```

The nullable JSON `surveys.pricing_snapshot` records inputs, currency, version, reward and recruitment cents, rate, minimum and whether it applied, urgency basis points and amount, subtotal, and total. Existing listings are not backfilled or repriced. New submissions save the quote with the draft before opening Checkout; payment confirmation preserves it. Funded quoted listings cannot change their price or participant cap through edit/builder routes; create a new listing for a new order.

Stripe must be configured for card-funded researcher publication and wallet top-ups, including raffles and volunteers. A funded wallet can publish without a new Stripe Checkout. Missing configuration leaves an unpaid draft and returns 503. Admin demo publication remains available without charging and is explicitly marked `admin_demo`. Stripe Checkout uses the snapshot total in cents and an idempotency key. Only a signed webhook confirming the expected session, amount, currency, and paid status publishes a quoted study. Visiting the success URL alone does not mark a study paid. Webhook retries do not reopen paid closed studies.

Pricing and Terms describe automatic seven-day approval for verified completions without quality or trust holds, and original-card refunds after closeout. Unresolved holds pause settlement for review. New orders default to wallet refunds, with original-card refunds selectable before settlement freezes. Legacy orders retain their original-card default.

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


## Phase three: researcher wallet and billing

Apply migration `202611140003`. `/publisher/wallet` keeps researcher funding separate from participant earnings. Cash movements are append-only integer-cent ledger entries; funding lots retain the original Stripe PaymentIntent. Top-ups use saved orders and stable idempotency keys. Only a signed, matching `checkout.session.completed` event credits cash; checkout redirects never credit funds. Repeated events and publication retries cannot charge twice. Wallet and study locks serialize spending; cash cannot exceed available funding lots. Unexpected external refunds or disputes pause further wallet spending pending support reconciliation.

Researchers can pay a quoted draft with wallet cash, or use the existing card checkout. An open card checkout must be expired before switching the draft to wallet; a paid checkout cannot switch. The payment records an immutable price snapshot and cash/promotion allocation. A server quote determines amounts; client-supplied balances or discounts are never trusted.

Coupons are created at `/admin/wallet` using existing admin authentication. Each has a positive USD credit amount, UTC expiry, and whole-number redemption cap; each researcher redeems it once. Promotional grants expire and offset only base platform recruitment fees during wallet payment. Participant rewards and urgency charges still require cash. Promotional credit is consumed earliest-expiry first, is never withdrawn or refunded, and is excluded from unused-funding cash refunds.

New purchases default to wallet refunds. The researcher can select original card(s) at purchase or change the destination in study details before the settlement freezes. Old orders with no saved destination keep original-card refunds. Wallet refunds credit only the cash portion once, restore its funding provenance, and do not call Stripe Refund. Original-card refunds for wallet orders are split proportionally over the original funding lots. Durable refund parts are committed before provider calls; retries reconcile metadata and fixed idempotency keys, and pending parts are polled. Failed provider parts require support review. Researcher cancellation still retains platform fees according to phase-two rules.

Cash history, top-up status, coupon history, and PDF downloads are available in Wallet & billing. Billing profiles apply to newly issued documents; immutable invoice snapshots preserve the buyer, issuer, line items, discount, and payment method. Top-up documents are funding receipts; each funded quoted listing has its own service invoice, avoiding a second charge for the same funding. Configure `INVOICE_ISSUER_NAME`, `INVOICE_ISSUER_ADDRESS`, and `INVOICE_ISSUER_EMAIL` for the actual issuing entity before production use. No tax registration or tax treatment is inferred. The bundled Noto Sans SC font supports English and Chinese billing text under the included OFL license.

Stripe test-mode webhook subscriptions also need `charge.refunded` and `charge.dispute.created`. Without Stripe test keys, top-ups and new card checkout stay unavailable; tests use provider mocks and do not constitute a real Stripe end-to-end run.

Validation: `python -m unittest tests.test_wallet` covers exact-once top-ups, funding validation, concurrent spending/coupon limits, promo restrictions, wallet refunds, multi-source card refund reconciliation, billing ownership and snapshots, signed webhook dispatch, and wallet publication. Existing pricing, publication, settlement and booking regressions also apply. Migration downgrade/upgrade is checked on an isolated database; browser and PDF rendering are reviewed locally before staging deployment.


### Accessing the isolated test site

Production users and OAuth credentials are not copied to staging. The staging login page explicitly uses email/password and explains that test accounts are separate. Optional service-only `STAGING_RESEARCHER_EMAIL` (a fictional `.invalid` address) and `STAGING_RESEARCHER_PASSWORD_HASH` (bcrypt) provision one ordinary researcher at startup. The staging entrypoint validates the environment and dedicated database before provisioning. It never resets existing passwords, grants admin access, or credits money. Supply these variables through Render's merge operation; they are deliberately omitted from the Blueprint so a future sync preserves them. Do not commit the generated password or hash. Email-code registration still requires a configured test mail provider.


### Wallet budget planner

Wallet opens with a planning-only budget calculator, followed by Payments & refunds, funding balances, and billing documents. Promotional credit history is hidden from this page; grant accounting and expiry are unchanged. The planner accepts a total budget including platform fees, target count, study format, and audience specificity. It finds the largest cent-denominated cash reward that fits the total by calling `calculate_quote`, the same server pricing used at publication. It shows full participant rewards, platform commission, standard total, eligible promo credit, existing cash, and the remaining top-up gap (respecting the $1 minimum). Survey recruitment is 20% with a **$5 minimum paid to the platform per participant**, added to the full reward; remote and in-person minimums remain $10 and $15. Estimates assume a flexible timeline. Targeted and highly niche audiences show a standard-audience reference only, require a manual quote before funding, and do not receive an invented surcharge or top-up recommendation. Calculation does not create a listing, debit funds, consume promotional credit, or initiate Checkout. Listing creation retains its existing price calculator and authoritative final quote.


### Listing budget and payment review

Survey (including built-in survey publication), remote interview, and in-person creation share a compact study-total summary and collapsed pricing rules. Researchers can directly set the full per-participant reward or optionally plan from a total budget. The planner reuses `/api/wallet/budget-estimate` with the current participant count, study type, and urgency. Applying it only fills the reward, closes the planner, and refreshes the authoritative final quote; it never publishes or initiates payment. Niche estimates remain manual references and cannot be applied automatically. Planner fields belong to a separate form so they do not enter publication data or block draft validation. The review step shows card or wallet cash due prominently, including eligible promotional credits for wallet payment. Published prices and settlement rules remain based on the saved server quote.


Stripe requests explicitly pin `2025-03-31.basil`, matching the configured snapshot webhook endpoint. The SDK otherwise selects its own newer API version: Stripe Python 17 defaults to Endive, which rejects the existing card-only `payment_method_types` Checkout parameter. Upgrade this contract and the webhook deliberately together, rather than inheriting a breaking API change from a package upgrade. Checkout diagnostics log only exception type and Stripe request ID, not provider messages or keys.


Provider resources are normalized to plain dictionaries at webhook, funding lookup, and refund reconciliation boundaries. Stripe Python 17 no longer exposes dictionary methods such as `.get()` on resource objects; tests construct real SDK resources and submit HMAC-signed events through the actual webhook verifier. The project uses Stripe Python 17.x consistently in development and staging. A successful Checkout redirect still does not credit funding; a failed callback is retried/replayed after the application fix, preserving exact-once credit.

### Staging Sandbox acceptance (2026-11-15)

Verified on the isolated staging service at commit `fd09303`: a $10 Stripe Sandbox top-up credited once after signed webhook delivery; manually replaying the same event returned HTTP 200 without adding credit. A $5 wallet-funded volunteer listing published and settled back to its original test card. A separate $5 card-funded volunteer listing published through its paid webhook and settled into the wallet. Both test listings were closed, and the final cash balance was $10 with no spending block. Study invoice PDF downloads were verified. These checks used Stripe Sandbox cards and generated no real charges; production resources were unchanged. The earlier provider-mocked regression checks remain complementary to this hosted integration run.

## Phase four: manual niche recruitment

Apply migration `202611150001`. Survey, built-in survey, remote and in-person publication offer a niche checkbox in the penultimate Budget step. Selecting it replaces immediate checkout with **Submit requirements**. The request remains a private draft, with an optional desired reward, an immutable audience description, and the existing targeting/scheduling fields. Submission tokens prevent duplicate requests and an existing request cannot be resubmitted as a standard purchase. Dashboard rows display Awaiting quote or Awaiting payment and open the owner-bound quote page. Submitted requests cannot be edited or deleted through regular researcher, builder-metadata, or admin listing-edit routes; create a fresh request if its scope needs to change.

The existing admin panel links to `/admin/niche`, protected by the same administrator key. The team enters the full participant reward, a manually chosen platform recruitment fee per participant, scope notes, and 1–30 days of validity (default seven). The survey/remote/in-person platform floors of $5/$10/$15 still apply; no automatic niche percentage is invented. Backend cent arithmetic applies the saved participant count and urgency policy and preserves an append-only offer version. The researcher sees the total first, with reward and platform fee separated, and explicitly accepts before Stripe Checkout. Wallet spending and coupons are not supported for these custom offers; unused funding can still return to Wallet or the original card under the existing settlement rules. Each completed paid quote receives the usual study invoice.

The admin can copy the researcher payment-page link; only the study owner can open and accept it. No email is sent automatically. Niche creation, pending requests and quote pages link to `vfsa@bu.edu` via **Contact our founder**. Where a listing exists, the mail subject includes its ID. The Wallet niche calculator also points to the request flow and founder email.

A replacement quote first confirms that the prior Stripe Checkout has expired (or expires it successfully). A completed payment prevents replacement until its webhook is reconciled. Creation attempts persist their fixed parameters and idempotency key before contacting Stripe, and unknown outcomes block repricing. Retry the same quote to reconcile that attempt. A signed paid webhook can also recover its matching session by authoritative Stripe lookup if the create response was lost. Do not roll an idempotency key to bypass an unresolved payment.

Checkout expires within 23 hours and never later than the offer expiry or study deadline; both relevant expiry dates are shown on the payment page. An expired payment link requires a replacement offer. Stripe session expiry is enforced at the provider, and stale quote IDs, wrong amounts/currencies/owners and unrelated sessions cannot publish. Only the matching signed paid callback saves the manual pricing snapshot and publishes; redirects do not publish. Duplicate events do not duplicate invoices or reopen closed studies. Settlement and original-card refunds reuse the saved snapshot and original PaymentIntent.

Validation: `tests.test_niche` covers idempotent private submission, access control, fee floors, manual totals and refunds, explicit acceptance, quote expiry/replacement, signed paid callbacks, no duplicate publication/invoices, stale/wrong payment rejection, lost-response reconciliation and both interview formats. `tests/test_niche_request.cjs` verifies switching between standard payment and manual request submission. Migration upgrade/downgrade/upgrade is checked on an isolated database; local browser checks cover request submission, admin quotation and the owner payment page. Provider calls in the new automated tests are mocked; they do not represent a new hosted Stripe charge.

## Release acceptance: 2026-11-21 (America/New_York)

Hosted Stripe Sandbox acceptance passed on `insighta-pricing-staging-8dsx` using the private preview researcher and disposable Listing #3. Request #1 received manual quote #1: one participant, $1 full reward, $5 platform recruitment minimum, $6 total, flexible timeline. The owner accepted the quote and chose original-card refunds. Stripe-hosted Sandbox Checkout collected $6; the signed webhook confirmed payment and published the study. Closing the study with zero completions produced a confirmed $6 original-card refund. The study is closed, with no active test recruitment. No live-mode charge or production mutation was performed.

The owner-only invoice `INS-STUDY-3` downloaded successfully and was visually checked: $1 reward, $5 platform recruitment, $0 urgency, $6 paid, $0 due. Refunds remain separately recorded in settlement history. The invoice currently uses the configured/default Insighta issuer details; confirm the legal issuer name, address and billing email before production rollout.

### Participant withdrawal safeguards

Migration `202611210001` adds durable participant withdrawal records. Apply it before enabling this version in production. Staging schema bootstrap creates the new table on startup. Withdrawals snapshot integer cents, the connected-account destination, approved response amounts and other trusted earnings (including feedback awards). A participant lock and unique active reservation serialize requests. Repeated requests use one stable Stripe idempotency key; unknown outcomes reconcile paginated provider history before another attempt. After 20 hours, unresolved attempts require support review rather than creating a new transfer. Only snapshotted responses are marked paid, preserving later earnings and pending reviews. Reserved rewards cannot be rejected or returned to review during reconciliation. Feedback awards are credited once.

`GET /api/withdrawal/status` returns the signed-in participant's latest attempt. Support may use `GET /admin/withdrawals` and `POST /admin/withdrawals/{id}/reconcile` with the existing `x-admin-key` header. Reconciliation only locates existing transfers; it never creates a transfer or manually forces a paid status. Keep credentials out of query strings and logs.

Connect onboarding callbacks use the deployment's base URL. Completion requires submitted details, payouts enabled and an active transfers capability. Participant UI correctly describes a transfer to the Stripe account; bank payout is a separate Stripe operation. The automated tests mock provider transfers: actual connected-account onboarding and bank receipt remain unverified. WorldFirst has no implemented payout API integration yet.

Validation: 77 financial tests (withdrawals, niche quotes, wallet, settlements, Stripe resources), 32 publication/staging tests and 16 frontend tests passed. The new migration passed isolated SQLite upgrade/downgrade/upgrade, including its other-earnings column. Production PostgreSQL migration execution remains part of the production release.

### Production release gates

- Production was inspected read-only: it still runs main commit `58516aa`, predating the pricing changes. Pricing phases one through four have been validated on staging, not released to production.
- The production service uses a paid Starter instance. Verify the settlement-worker environment toggle and runtime logs after release; the free staging worker pauses while its service sleeps. Do not run general queued production jobs as a smoke test because they can send real email.
- Confirm live Stripe keys, signed webhook destination/API version, and participant payout jurisdiction/account configuration. Sandbox recruitment acceptance does not prove live settlement or bank payout readiness. Formal invoice issuer details are tracked separately for lawyer review below.
- Rotate the staging admin credential after acceptance: browser tooling unexpectedly echoed it during the authorized login workflow. No production credential was read. Do not reuse the staging key in production.

### Updated launch scope: Stripe first

User decision on 2026-11-21: proceed with Stripe for the initial release; postpone the alternative banking/provider integration. WorldFirst is no longer the planned provider. PingPong is the intended later option and is not a prerequisite for the initial Stripe release. No PingPong integration or compatibility with Stripe has been verified. The current application contains no user-facing promises that either WorldFirst or PingPong is available.

Keep researcher card payments, wallet funding, listing payments and original-card refunds on the existing Stripe paths. Keep participant withdrawal on the existing Stripe Connect path; an actual connected-account onboarding and payout check remains outstanding before treating withdrawals as bank-ready. Platform settlement to its own bank and participant payouts are distinct flows. Evaluate the intended PingPong account/provider role, supported account holder, country, currency and Stripe compatibility later, before changing payment routing. This decision does not deploy production or move funds.

Formal invoice issuer information and tax/document wording are deferred to the user's discussion with their lawyer. Do not invent legal entity, address or tax registration data, or describe the current generated PDFs as lawyer-approved tax invoices. The existing invoice configuration requirements describe the eventual formal setup, not a completed legal review.
