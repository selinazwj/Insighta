"""Read-only inventory of legacy identity-document references.

This intentionally prints counts only: never participant emails, document
paths, or document contents. Deletion remains a separate, explicitly approved
operation after backup/retention requirements are confirmed.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text


def main() -> None:
    database_url = (os.environ.get("AUDIT_DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit(
            "Set AUDIT_DATABASE_URL explicitly. The audit refuses to use the "
            "application's default database connection."
        )
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT
                    COALESCE(NULLIF(LOWER(TRIM(id_review_status)), ''), 'unset') AS review_status,
                    COUNT(*) AS reference_count
                FROM users
                WHERE id_document_path IS NOT NULL
                  AND TRIM(id_document_path) != ''
                GROUP BY COALESCE(NULLIF(LOWER(TRIM(id_review_status)), ''), 'unset')
            """)).all()
        review_statuses = {
            str(status): int(count or 0) for status, count in rows
        }
        payload = {
            "legacy_reference_count": sum(review_statuses.values()),
            "review_status_counts": dict(sorted(review_statuses.items())),
            "raw_id_routes_enabled": False,
            "contains_paths_or_user_identifiers": False,
            "next_step": (
                "Confirm legal retention and backup requirements before creating "
                "a separately approved deletion job."
            ),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
