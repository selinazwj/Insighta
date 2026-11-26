"""Register every persistent entity without importing the web application.

Migration and schema tooling must see the same metadata as the running app,
including the independently owned AI, niche and withdrawal tables.
"""

from app.models import Base
from app.ai_growth import models as _ai_growth_models
from app import niche_models as _niche_models
from app import withdrawals as _withdrawal_models

__all__ = ["Base"]
