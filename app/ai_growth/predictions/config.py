"""Prediction model version identity."""
from __future__ import annotations

from app.ai_growth.llm import claude_model_name


MODEL_VERSION = f"claude-llm-only:{claude_model_name()}"
