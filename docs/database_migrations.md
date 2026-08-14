# Database Migrations

This project uses Alembic for production database schema changes.

## Run Migrations

Use the same `DATABASE_URL` that the app uses:

```bash
DATABASE_URL=postgresql://... alembic upgrade head
```

Or use the project wrapper (preferred in deploy scripts):

```bash
DATABASE_URL=postgresql://... python scripts/run_migrations.py
```

For local SQLite development, omit `DATABASE_URL`:

```bash
alembic upgrade head
```

Confirm a single head before deploying:

```bash
alembic heads
```

## Render Deploy Hook

Production runs on Render with auto-deploy from `main`. Add migrations to the
Web Service so every deploy upgrades schema before traffic hits new code:

1. Prefer **Pre-Deploy Command**:

   ```bash
   python scripts/run_migrations.py
   ```

2. If the plan has no Pre-Deploy, append to **Build Command**, for example:

   ```bash
   pip install -r requirements.txt && python scripts/run_migrations.py
   ```

3. Keep **Start Command** as the app process only (for example `uvicorn`), not
   migrations.

`DATABASE_URL` must be the Render PostgreSQL URL. Do not point production at
SQLite.

## First Production Rollout

The current app still contains startup-time compatibility helpers that create
tables and add missing columns. During the first rollout, run:

```bash
DATABASE_URL=postgresql://... python scripts/run_migrations.py
```

Current migrations cover growth-path indexes, the `background_jobs` table, and
removal of legacy ID-verification columns. Index creation skips tables that do
not exist yet, so the first revision is safe across partially migrated
environments.

Only after migrations are in the deploy flow **and** `alembic upgrade head`
succeeds against production PostgreSQL should you disable startup schema
bootstrap:

```bash
INSIGHTA_AUTO_SCHEMA_BOOTSTRAP=0
```

Also set (or confirm) these production env vars:

```bash
INSIGHTA_SLOW_REQUEST_MS=1000
INSIGHTA_DASHBOARD_CANDIDATE_LIMIT=100
```

Keep `INSIGHTA_AUTO_SCHEMA_BOOTSTRAP` enabled (default `1`) for local
development until all legacy `ensure_*` schema helpers have been converted into
Alembic migrations.

## New Schema Changes

Create a new migration for every database structure change:

```bash
alembic revision -m "describe change"
```

Then edit the generated file with explicit `upgrade()` and `downgrade()` steps.
Avoid adding new `ALTER TABLE` logic to app startup code.
