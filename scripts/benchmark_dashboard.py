#!/usr/bin/env python3
"""Request-level dashboard benchmark for 10 / 100 / 1000 surveys.

Runs against a disposable SQLite database with AI ranking enabled. The cache is
cold and the background worker is not executed, so this measures the exact
non-blocking request path: bulk cache read + local fallback + async job enqueue.
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_db_file = tempfile.NamedTemporaryFile(prefix="insighta-dashboard-bench-", suffix=".db", delete=False)
_db_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.name}"
os.environ["INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS"] = "1"
os.environ["INSIGHTA_DASHBOARD_CANDIDATE_LIMIT"] = "100"
os.environ["INSIGHTA_DASHBOARD_SCAN_LIMIT"] = "500"
os.environ.setdefault("INSIGHTA_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient
from sqlalchemy import event

from api import main
from app.models import Survey, User


DESKTOP_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
RUNS = 5


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile_value))))
    return ordered[index]


def reset_and_seed(count: int) -> int:
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    db = main.SessionLocal()
    publisher = User(email=f"publisher-{count}@bench.test", password="x")
    participant = User(
        email=f"participant-{count}@bench.test",
        password="x",
        birth_year="1998",
        birth_month="1",
        field="Computer Science & Engineering",
        language="English",
        participation_format="both",
        device_type="any",
    )
    db.add_all([publisher, participant])
    db.flush()
    now = datetime.utcnow()
    db.add_all([
        Survey(
            publisher_id=publisher.id,
            title=f"Benchmark Study {index}",
            description="Synthetic dashboard performance benchmark fixture.",
            form_url=f"https://example.com/{count}/{index}",
            category="research",
            estimated_time=10,
            share_slug=f"bench-{count}-{index}",
            reward_amount=5.0,
            target_responses=100,
            status="published",
            payment_status="paid",
            published_at=now - timedelta(seconds=index),
        )
        for index in range(count)
    ])
    db.commit()
    participant_id = participant.id
    db.close()
    return participant_id


def benchmark_path(client: TestClient, path: str, user_agent: str, participant_id: int) -> tuple[float, float, float]:
    durations: list[float] = []
    query_counts: list[int] = []

    # Warm template/import caches before sampling.
    warm = client.get(path, cookies={"user_id": str(participant_id)}, headers={"user-agent": user_agent})
    if warm.status_code != 200:
        raise RuntimeError(f"Warm-up failed for {path}: HTTP {warm.status_code}")

    for _ in range(RUNS):
        query_count = 0

        def before_cursor_execute(*_args, **_kwargs):
            nonlocal query_count
            query_count += 1

        event.listen(main.engine, "before_cursor_execute", before_cursor_execute)
        started = time.perf_counter()
        try:
            response = client.get(
                path,
                cookies={"user_id": str(participant_id)},
                headers={"user-agent": user_agent},
            )
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            event.remove(main.engine, "before_cursor_execute", before_cursor_execute)
        if response.status_code != 200:
            raise RuntimeError(f"Benchmark failed for {path}: HTTP {response.status_code}")
        durations.append(elapsed_ms)
        query_counts.append(query_count)

    return (
        statistics.median(durations),
        percentile(durations, 0.95),
        statistics.mean(query_counts),
    )


def main_benchmark() -> None:
    client = TestClient(main.app)
    print("mode=ai_enabled,cold_cache,worker_not_run,candidate_limit=100,scan_limit=500")
    print("surveys\tpath\tp50_ms\tp95_ms\tavg_sql")
    try:
        for count in (10, 100, 1000):
            participant_id = reset_and_seed(count)
            for path, user_agent in (("/dashboard", DESKTOP_UA), ("/dashboard/mobile", MOBILE_UA)):
                p50, p95, avg_sql = benchmark_path(client, path, user_agent, participant_id)
                print(f"{count}\t{path}\t{p50:.2f}\t{p95:.2f}\t{avg_sql:.1f}")
    finally:
        try:
            Path(_db_file.name).unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main_benchmark()
