# Insighta

Insighta is a FastAPI + Jinja2 research recruitment and participation platform. Researchers can publish surveys or interviews, define participant criteria, manage responses, and track study status. Participants can build a profile, discover suitable studies, complete research tasks, and follow response or reward status.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --reload
```

The default local database is `sqlite:///./survey.db`. Review every production environment variable in `.env.example`; never commit `.env` or real credentials.

## Public SEO architecture

The project contains a reusable SEO layer in `app/seo.py` and a shared Jinja head partial in `app/templates/_seo_head.html`.

Public, indexable routes:

- `/` — canonical responsive homepage
- `/participant` — participant landing page
- `/studies` — crawlable directory of published research opportunities
- `/studies/{category_slug}` — category landing pages
- `/r/{share_slug}` — published study detail pages
- `/about`, `/privacy`, `/terms`
- `/robots.txt`, `/sitemap.xml`

Account, admin, payment, dashboard, survey-response, and error pages default to `noindex` in both HTML metadata and HTTP `X-Robots-Tag` headers. Closed study pages remain accessible to existing users but are removed from the sitemap and marked `noindex`.

Set the preferred production origin before deployment:

```env
BASE_URL=https://insightaco.org
SEO_SITE_URL=https://insightaco.org
SEO_INDEX_STUDIES=true
```

Optional search-console verification values are supported through `GOOGLE_SITE_VERIFICATION` and `BING_SITE_VERIFICATION`.

## SEO regression checks

The dependency-free static audit verifies metadata plumbing, public templates, structured-data safety, sitemap/robots routes, local assets, environment documentation, and obvious credential regressions:

```bash
python scripts/seo_audit.py --strict
```

The end-to-end smoke test starts the real FastAPI application against an isolated temporary SQLite database and verifies public routes, mobile behavior, canonical URLs, JSON-LD, sitemap membership, and noindex boundaries:

```bash
python scripts/smoke_test_seo.py
```

GitHub Actions runs both checks from `.github/workflows/seo-check.yml`.

## Deployment checklist

1. Configure one HTTPS canonical origin in both `BASE_URL` and `SEO_SITE_URL`.
2. Confirm production `DATABASE_URL` is PostgreSQL (Render Postgres), not SQLite.
3. Add Render Pre-Deploy (or Build) command: `python scripts/run_migrations.py`. See `docs/database_migrations.md`.
4. Set production env: `INSIGHTA_SLOW_REQUEST_MS=1000`, `INSIGHTA_DASHBOARD_CANDIDATE_LIMIT=100`. Set `INSIGHTA_AUTO_SCHEMA_BOOTSTRAP=0` only after migrations succeed on production.
5. Generate a long random `ADMIN_KEY`; the application no longer accepts a built-in default key.
6. Configure Stripe, email, OAuth, and Anthropic credentials only in the deployment secret store.
7. Set `CRON_SECRET` so the job cron can call `/internal/jobs/run-due`.
8. Set `BACKGROUND_JOB_ALERT_EMAIL` if job failure alerts should go somewhere other than `SUPPORT_ALERT_EMAIL`.
9. Deploy, then inspect `/robots.txt` and `/sitemap.xml` on the production host.
10. In Render Logs, confirm request lines from `insighta.api` (`request method=...`); slow requests appear as `slow_request` when over the threshold.
11. Open `/admin#jobs`, confirm job stats load, and click `Run due jobs` once when ready to verify the runner.
12. Add the site to Google Search Console and Bing Webmaster Tools; submit the sitemap.
13. Validate representative homepage, directory, category, and study URLs with search-engine inspection tools.
14. Replace the product-aligned Privacy and Terms drafts with counsel-approved, entity- and jurisdiction-specific versions before public production use.

See `SEO_IMPLEMENTATION.md` for the full SEO audit, implementation map, and operating guidance. See `docs/background_jobs_runbook.md` for background job rollout, verification, and rollback steps. The broader original product documentation remains in `Insighta_README_current_v2.md`.

Participant identity verification setup and API flow: [Didit integration](docs/participant_didit.md).
