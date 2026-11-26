"""Claude prediction result validation and unavailable payloads."""
from __future__ import annotations

from typing import Any
from typing import Optional
from app.models import Survey
from app.models import User
from app.ai_growth.llm import claude_model_name
from app.ai_growth.segments import user_segment_label

from .config import (
    MODEL_VERSION,
)

def _clamp_probability(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str) and value.strip().endswith("%"):
            value = float(value.strip().rstrip("%")) / 100.0
        else:
            value = float(value)
    except Exception:
        value = default
    return round(max(0.0, min(1.0, value)), 4)

def _clean_confidence(value: Any) -> str:
    value = str(value or "low").strip().lower()
    return value if value in {"low", "medium", "high"} else "low"

def _clean_list(value: Any, fallback: Optional[list[str]] = None, limit: int = 5) -> list[str]:
    fallback = fallback or []
    if not isinstance(value, list):
        value = fallback
    cleaned = [str(x).strip() for x in value if str(x).strip()]
    return cleaned[:limit]

def _unavailable_prediction(survey: Survey, user: User, error: str) -> dict:
    return {
        "survey_id": getattr(survey, "id", None),
        "participant_id": getattr(user, "id", None),
        "completion_probability": 0.0,
        "confidence": "low",
        "segment_label": user_segment_label(user),
        "top_reasons": [],
        "risk_reasons": [error],
        "recommended_action": "Configure Claude by setting ANTHROPIC_API_KEY and AI_GROWTH_CLAUDE_MODEL, then recompute predictions.",
        "ranking_note": "Claude prediction unavailable; no local rule score was used.",
        "features": {"llm_error": error, "llm_only": True},
        "model_version": MODEL_VERSION,
        "cached": False,
        "llm_ok": False,
    }

def _normalize_llm_prediction(raw: dict, *, survey: Survey, user: User, context: Optional[dict] = None) -> dict:
    context = context or {}
    probability = _clamp_probability(raw.get("completion_probability"), default=0.0)
    result = {
        "survey_id": getattr(survey, "id", None),
        "participant_id": getattr(user, "id", None),
        "completion_probability": probability,
        "confidence": _clean_confidence(raw.get("confidence")),
        "segment_label": raw.get("segment_label") or user_segment_label(user),
        "top_reasons": _clean_list(raw.get("top_reasons"), limit=5),
        "risk_reasons": _clean_list(raw.get("risk_reasons"), limit=5),
        "recommended_action": str(raw.get("recommended_action") or "").strip(),
        "ranking_note": str(raw.get("ranking_note") or "").strip(),
        "features": {
            "llm_only": True,
            "provider": "anthropic",
            "model": claude_model_name(),
            "raw_context_summary": context,
            "llm_output": {
                "recommended_action": str(raw.get("recommended_action") or "").strip(),
                "ranking_note": str(raw.get("ranking_note") or "").strip(),
            },
        },
        "model_version": MODEL_VERSION,
        "cached": False,
        "llm_ok": True,
    }
    if not result["top_reasons"]:
        result["top_reasons"] = ["Claude identified this as a relevant candidate based on the supplied survey and behavior context."]
    if not result["recommended_action"]:
        result["recommended_action"] = "Use this Claude prediction for ranking; collect more completion data to improve future judgments."
    return result

def _extract_items_by_id(data: dict, list_key: str, id_key: str) -> dict[int, dict]:
    items = data.get(list_key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    by_id: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            key = int(item.get(id_key))
        except Exception:
            continue
        by_id[key] = item
    return by_id
