"""Claude/Anthropic integration for Insighta AI Growth.

This module is intentionally LLM-only: it does not compute any weighted rule
score. The surrounding prediction module only prepares sanitized facts and raw
platform statistics, then asks Claude to produce probabilities, reasons, risks,
and recommendations.

Required environment variable:
    ANTHROPIC_API_KEY

Optional environment variables:
    AI_GROWTH_CLAUDE_MODEL          default: claude-sonnet-4-5
    AI_GROWTH_LLM_MAX_TOKENS        default: 1400
    AI_GROWTH_LLM_TEMPERATURE       default: 0.1
    AI_GROWTH_LLM_BATCH_SIZE        default: 25
    AI_GROWTH_LLM_CACHE_HOURS       default: 6
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

try:  # Keep the app importable even before requirements are installed.
    import anthropic
except Exception:  # pragma: no cover - only hit in incomplete deployments.
    anthropic = None


DEFAULT_MODEL = "claude-sonnet-4-5"


@dataclass
class LLMCallResult:
    data: Optional[dict]
    error: Optional[str] = None
    raw_text: str = ""


def claude_model_name() -> str:
    return os.environ.get("AI_GROWTH_CLAUDE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def llm_cache_hours() -> int:
    try:
        return max(0, int(os.environ.get("AI_GROWTH_LLM_CACHE_HOURS", "6")))
    except Exception:
        return 6


def llm_batch_size() -> int:
    try:
        return max(1, min(100, int(os.environ.get("AI_GROWTH_LLM_BATCH_SIZE", "25"))))
    except Exception:
        return 25


def llm_configured() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def _message_to_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    # Claude is instructed to return only JSON, but this makes the integration
    # robust if a deployment/system prompt adds incidental text.
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def call_claude_json(*, system: str, payload: dict, max_tokens: Optional[int] = None) -> LLMCallResult:
    """Call Claude and parse a JSON object response.

    The function returns errors as data instead of raising so web pages can keep
    rendering while clearly exposing configuration/API failures.
    """
    if anthropic is None:
        return LLMCallResult(data=None, error="The 'anthropic' package is not installed. Run: pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return LLMCallResult(data=None, error="ANTHROPIC_API_KEY is not set. Add it to .env or deployment environment variables.")

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model=claude_model_name(),
            max_tokens=max_tokens or _safe_int_env("AI_GROWTH_LLM_MAX_TOKENS", 1400),
            temperature=_safe_float_env("AI_GROWTH_LLM_TEMPERATURE", 0.1),
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - network/API dependent.
        return LLMCallResult(data=None, error=f"Claude API request failed: {exc}")

    text = _message_to_text(message)
    data = _extract_json(text)
    if data is None:
        return LLMCallResult(data=None, error="Claude response was not valid JSON.", raw_text=text)
    return LLMCallResult(data=data, raw_text=text)


# ---------------------------------------------------------------------------
# Sanitized payload helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def safe_survey_payload(survey: Any) -> dict:
    """Survey payload safe to send to Claude.

    We intentionally omit sensitive target values such as ethnicity, sexual
    orientation, health diagnosis, smoking, and cannabis. Local hard matching
    may still use those fields for eligibility when the publisher configured
    them, but Claude should not use or explain sensitive traits.
    """
    form_url = (_get(survey, "form_url", "") or "").strip()
    return {
        "id": _get(survey, "id"),
        "title": _get(survey, "title"),
        "description": _get(survey, "description"),
        "task_type": _get(survey, "task_type"),
        "category": _get(survey, "category"),
        "estimated_time_minutes": _get(survey, "estimated_time"),
        "reward_amount": _get(survey, "reward_amount"),
        "target_responses": _get(survey, "target_responses"),
        "current_responses": _get(survey, "current_responses"),
        "urgency_level": _get(survey, "urgency_level"),
        "incentive_type": _get(survey, "incentive_type"),
        "status": _get(survey, "status"),
        "published_at": _get(survey, "published_at"),
        "destination_type": "builtin" if form_url == "__builtin__" else ("external_or_interview" if form_url else "unknown"),
        "non_sensitive_targeting": {
            "age_range": _get(survey, "target_age_range"),
            "education_min": _get(survey, "target_education_min"),
            "education_max": _get(survey, "target_education_max"),
            "field": _get(survey, "target_field"),
            "status": _get(survey, "target_status"),
            "state": _get(survey, "target_state"),
            "language": _get(survey, "target_language"),
            "student_status": _get(survey, "target_student_status"),
            "year_in_school": _get(survey, "target_year_in_school"),
            "international_domestic": _get(survey, "target_international_domestic"),
            "experience_tags": _get(survey, "target_experience_tags"),
            "participation_format": _get(survey, "target_participation_format"),
            "device": _get(survey, "target_device"),
        },
        "has_sensitive_targeting": any(
            bool(_get(survey, name))
            for name in [
                "target_ethnicity",
                "target_sexual_orientation",
                "target_mental_health_diagnosis",
                "target_physical_health_diagnosis",
                "target_smoking",
                "target_cannabis_use",
            ]
        ),
    }


def safe_user_payload(user: Any) -> dict:
    """Participant payload safe to send to Claude.

    Never send identifiers, email, passwords, OAuth IDs, payout fields, or
    sensitive profile fields. The participant_id is passed separately only as a
    local row key for JSON mapping.
    """
    return {
        "age_range": _get(user, "age_range"),
        "education_level": _get(user, "education_level"),
        "field": _get(user, "field"),
        "status": _get(user, "status"),
        "state": _get(user, "state"),
        "language": _get(user, "language"),
        "student_status": _get(user, "student_status"),
        "year_in_school": _get(user, "year_in_school"),
        "international_domestic": _get(user, "international_domestic"),
        "experience_tags": _get(user, "experience_tags"),
        "participation_format": _get(user, "participation_format"),
        "device_type": _get(user, "device_type"),
        "sport_type": _get(user, "sport_type"),
        "sport_frequency": _get(user, "sport_frequency"),
        "created_at": _get(user, "created_at"),
    }


# ---------------------------------------------------------------------------
# Prompt entry points
# ---------------------------------------------------------------------------

PREDICT_ONE_SYSTEM = """
You are Insighta's LLM-only survey completion prediction engine.

Estimate the probability that this participant will successfully complete this survey/task using ONLY the factual JSON provided. Do not use any hidden formula, local rule score, or demographic stereotype. Treat local eligibility as a hard pre-filter supplied by the platform.

Return ONLY valid JSON with this exact shape:
{
  "completion_probability": 0.0,
  "confidence": "low|medium|high",
  "top_reasons": ["..."],
  "risk_reasons": ["..."],
  "recommended_action": "...",
  "ranking_note": "..."
}

Guidelines:
- completion_probability must be a number from 0 to 1.
- Keep reasons practical: task length, reward clarity, device fit, prior completion behavior, category familiarity, external return friction, survey clarity.
- Do not mention or infer sensitive traits, and do not recommend excluding protected classes.
- If evidence is sparse, lower confidence instead of inventing details.
""".strip()

PREDICT_PARTICIPANTS_SYSTEM = """
You are Insighta's LLM-only respondent ranking engine.

For one survey and many candidate participants, estimate each participant's completion probability using ONLY the factual JSON provided. Do not use a weighted rule score. The platform has already applied local eligibility filters.

Return ONLY valid JSON with this exact shape:
{
  "predictions": [
    {
      "participant_id": 123,
      "completion_probability": 0.0,
      "confidence": "low|medium|high",
      "top_reasons": ["..."],
      "risk_reasons": ["..."],
      "recommended_action": "...",
      "ranking_note": "..."
    }
  ]
}

Guidelines:
- Include exactly one prediction object for every input participant_id.
- completion_probability must be a number from 0 to 1.
- Use concise, product-actionable reasons.
- Do not mention or infer sensitive traits, and do not recommend excluding protected classes.
""".strip()

RANK_SURVEYS_SYSTEM = """
You are Insighta's LLM-only participant recommendation engine.

For one participant and many eligible surveys, estimate the likelihood that the participant will complete each survey. Use ONLY the factual JSON provided. Do not use urgency/date/rule weighting as a scoring formula; make an LLM judgment from the task, behavior context, and survey metadata.

Return ONLY valid JSON with this exact shape:
{
  "recommendations": [
    {
      "survey_id": 123,
      "completion_probability": 0.0,
      "confidence": "low|medium|high",
      "top_reasons": ["..."],
      "risk_reasons": ["..."],
      "recommended_action": "...",
      "ranking_note": "..."
    }
  ]
}

Guidelines:
- Include exactly one recommendation object for every input survey_id.
- completion_probability must be a number from 0 to 1.
- Recommend surveys that are likely to be completed, not merely urgent.
- Do not mention or infer sensitive traits, and do not recommend excluding protected classes.
""".strip()

SUMMARY_SYSTEM = """
You are Insighta's LLM-only publisher analytics assistant.

Given a survey, LLM participant predictions, and raw funnel facts, write a publisher-facing summary and practical recommendation. Use only the JSON provided.

Return ONLY valid JSON with this exact shape:
{
  "completion_probability": 0.0,
  "confidence": "low|medium|high",
  "segment_label": "...",
  "top_reasons": ["..."],
  "risk_reasons": ["..."],
  "recommended_action": "...",
  "audience_strategy": "..."
}

Guidelines:
- completion_probability should summarize expected completion probability across the current candidate pool.
- Prefer actionable marketplace levers: reward, duration, description clarity, device compatibility, target breadth, external return tracking.
- Do not mention or infer sensitive traits, and do not recommend excluding protected classes.
""".strip()

PREVIEW_SYSTEM = """
You are Insighta's LLM-only preview engine for unsaved survey drafts.

Estimate expected completion probability and give publishing advice from the draft survey and a sanitized sample of the current participant pool. Use ONLY the JSON provided.

Return ONLY valid JSON with this exact shape:
{
  "completion_probability": 0.0,
  "confidence": "low|medium|high",
  "segment_label": "...",
  "top_reasons": ["..."],
  "risk_reasons": ["..."],
  "recommended_action": "...",
  "audience_strategy": "..."
}

Guidelines:
- completion_probability must be a number from 0 to 1.
- If the participant sample is small or incomplete, use low confidence.
- Do not mention or infer sensitive traits, and do not recommend excluding protected classes.
""".strip()


def predict_one_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=PREDICT_ONE_SYSTEM, payload=payload, max_tokens=1000)


def predict_participants_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=PREDICT_PARTICIPANTS_SYSTEM, payload=payload, max_tokens=2200)


def rank_surveys_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=RANK_SURVEYS_SYSTEM, payload=payload, max_tokens=2200)


def summarize_survey_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=SUMMARY_SYSTEM, payload=payload, max_tokens=1200)


def preview_survey_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=PREVIEW_SYSTEM, payload=payload, max_tokens=1200)
