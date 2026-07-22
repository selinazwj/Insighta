"""Run Alembic migrations with the app's DATABASE_URL.

Usage:
    python scripts/run_migrations.py
    DATABASE_URL=postgresql://... python scripts/run_migrations.py
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
