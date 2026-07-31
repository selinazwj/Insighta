# Database Migrations

This project uses Alembic for production database schema changes.

## Run Migrations

Use the same `DATABASE_URL` that the app uses:

```bash
DATABASE_URL=postgresql://... alembic upgrade head
```

Or use the project wrapper:

```bash
DATABASE_URL=postgresql://... python scripts/run_migrations.py
```

For local SQLite development, omit `DATABASE_URL`:

```bash
alembic upgrade head
```

## First Production Rollout

The current app still contains startup-time compatibility helpers that create
tables and add missing columns. During the first rollout, run:

```bash
DATABASE_URL=postgresql://... alembic upgrade head
```

This first migration adds high-value indexes for dashboard, analytics,
notifications, quality review, and AI-growth paths. It skips indexes for tables
that do not exist yet, so it is safe across partially migrated environments.

After migrations are part of the deployment flow, production can disable the
legacy startup-time schema bootstrap:

```bash
INSIGHTA_AUTO_SCHEMA_BOOTSTRAP=0
```

Keep the default enabled for local development until all legacy one-off schema
helpers have been converted into Alembic migrations.

## New Schema Changes

Create a new migration for every database structure change:

```bash
alembic revision -m "describe change"
```

Then edit the generated file with explicit `upgrade()` and `downgrade()` steps.
Avoid adding new `ALTER TABLE` logic to app startup code.
