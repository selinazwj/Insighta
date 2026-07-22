"""Controls legacy startup-time schema bootstrapping.

Alembic migrations are the preferred production path. This flag keeps the
existing local/dev behavior available while allowing production deployments to
disable import-time table creation and ALTER TABLE compatibility helpers.
"""

from __future__ import annotations

import os


def auto_schema_bootstrap_enabled() -> bool:
    value = os.environ.get("INSIGHTA_AUTO_SCHEMA_BOOTSTRAP", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
