#!/usr/bin/env python3
"""Run end-to-end SEO route checks against an isolated temporary database.

This script imports the real FastAPI app, creates published and closed research
listings, renders every public page, and verifies canonical URLs, metadata,
structured data, sitemap membership, mobile behavior, and noindex boundaries.

Run from the project root after installing requirements:
    python scripts/smoke_test_seo.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def assert_contains(response, needle: str, label: str) -> None:
    if needle not in response.text:
        raise AssertionError(f"{label}: expected {needle!r} in response body")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="insighta-seo-") as temp_dir:
        db_path = Path(temp_dir) / "seo-smoke.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
        os.environ["SEO_SITE_URL"] = "https://seo-test.insighta.invalid"
        os.environ["BASE_URL"] = "https://seo-test.insighta.invalid"
        os.environ["SEO_INDEX_STUDIES"] = "true"
        os.environ["SURVEY_START_FOLLOWUP_POLL_SECONDS"] = "3600"

        from fastapi.testclient import TestClient
        from api.main import app
        from app.database import SessionLocal
        from app.models import Survey, User

        now = datetime.now(UTC).replace(tzinfo=None)
        with SessionLocal() as db:
            publisher = User(email="publisher@seo-test.invalid", password="not-a-login-hash", username="SEO Test Publisher")
            db.add(publisher)
            db.flush()
            db.add_all([
                Survey(
                    publisher_id=publisher.id,
                    title="Remote Work Habits Study",
                    description="A study about how hybrid work affects focus and collaboration.",
                    form_url="https://forms.invalid/remote-work",
                    task_type="survey",
                    category="market",
                    estimated_time=12,
                    share_slug="remote-work-habits",
                    reward_amount=15,
                    target_responses=30,
                    current_responses=2,
                    status="published",
                    payment_status="paid",
                    published_at=now,
                    image_url="/static/habit.png",
                    target_age_range="18-44",
                    target_state="Any U.S. state",
                    target_field="Any field",
                    target_status="Employed",
                ),
                Survey(
                    publisher_id=publisher.id,
                    title="Closed Campus Study",
                    description="A completed campus study retained for existing participants.",
                    form_url="__builtin__",
                    task_type="survey",
                    category="academic",
                    estimated_time=5,
                    share_slug="closed-campus-study",
                    reward_amount=5,
                    target_responses=10,
                    current_responses=10,
                    status="closed",
                    payment_status="paid",
                    published_at=now,
                ),
            ])
            db.commit()

        expected_statuses = {
            "/": 200,
            "/participant": 200,
            "/studies": 200,
            "/studies/market": 200,
            "/about": 200,
            "/privacy": 200,
            "/terms": 200,
            "/robots.txt": 200,
            "/sitemap.xml": 200,
            "/r/remote-work-habits": 200,
            "/r/closed-campus-study": 200,
            "/login": 200,
            "/studies/not-a-category": 404,
            "/r/not-found": 404,
        }

        with TestClient(app) as client:
            for path, expected in expected_statuses.items():
                response = client.get(path, headers={"user-agent": "Mozilla/5.0 (iPhone; Mobile)"})
                if response.status_code != expected:
                    raise AssertionError(f"{path}: expected {expected}, got {response.status_code}: {response.text[:300]}")

            home = client.get("/", headers={"user-agent": "Googlebot-Mobile"})
            if home.history:
                raise AssertionError("Mobile crawler was redirected away from the canonical homepage")
            assert_contains(home, '<link rel="canonical" href="https://seo-test.insighta.invalid/">', "homepage canonical")
            assert_contains(home, '<meta name="description"', "homepage description")
            assert_contains(home, '"@context":"https://schema.org"', "schema context")
            assert_contains(home, '"@type":"Organization"', "organization schema")

            directory = client.get("/studies")
            assert_contains(directory, "/r/remote-work-habits", "study directory link")
            assert_contains(directory, '<h1>Open research studies</h1>', "study directory H1")

            published = client.get("/r/remote-work-habits")
            assert_contains(published, '<link rel="canonical" href="https://seo-test.insighta.invalid/r/remote-work-habits">', "study canonical")
            assert_contains(published, '"@type":"ResearchProject"', "research project schema")
            assert_contains(published, '<img class="poster-image"', "crawlable study image")
            if published.headers.get("x-robots-tag"):
                raise AssertionError("Published study unexpectedly received X-Robots-Tag")

            closed = client.get("/r/closed-campus-study")
            assert_contains(closed, '<meta name="robots" content="noindex, follow, noarchive">', "closed study noindex meta")
            if not closed.headers.get("x-robots-tag", "").startswith("noindex"):
                raise AssertionError("Closed study is missing HTTP noindex protection")

            login = client.get("/login")
            assert_contains(login, '<meta name="robots" content="noindex, nofollow, noarchive">', "private page noindex meta")
            if not login.headers.get("x-robots-tag", "").startswith("noindex"):
                raise AssertionError("Private page is missing HTTP noindex protection")

            for error_path in ("/studies/not-a-category", "/r/not-found"):
                error_response = client.get(error_path)
                if not error_response.headers.get("x-robots-tag", "").startswith("noindex"):
                    raise AssertionError(f"{error_path}: missing noindex on error response")

            robots = client.get("/robots.txt")
            assert_contains(robots, "Sitemap: https://seo-test.insighta.invalid/sitemap.xml", "robots sitemap directive")

            sitemap = client.get("/sitemap.xml")
            assert_contains(sitemap, "https://seo-test.insighta.invalid/r/remote-work-habits", "published study in sitemap")
            if "closed-campus-study" in sitemap.text:
                raise AssertionError("Closed study should not be present in sitemap")

    print("SEO smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
