#!/usr/bin/env python3
"""Create AI Growth tables manually.

Normally this is not required because app.ai_growth.routes calls Base.metadata.create_all
when the app starts. Use this if you want to create tables before launching.
"""

from app.database import engine
from app.models import Base
import app.ai_growth.models  # noqa: F401 registers additive models

Base.metadata.create_all(bind=engine)
print("[DONE] AI Growth tables are ready.")
