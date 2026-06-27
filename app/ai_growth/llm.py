"""Claude/Anthropic integration for Insighta AI Growth.

This module is intentionally LLM-only: it does not compute any weighted rule
score. The surrounding prediction module prepares sanitized facts and raw
platform statistics, asks Claude for compact probability judgments, then expands
reason codes and UI guidance locally.

Required environment variable:
    ANTHROPIC_API_KEY

Optional environment variables:
    AI_GROWTH_CLAUDE_MODEL          default: claude-haiku-4-5-20251001
    AI_GROWTH_LLM_MAX_TOKENS        default: 1200
    AI_GROWTH_LLM_TEMPERATURE       default: 0.1
    AI_GROWTH_LLM_BATCH_SIZE        default: 20
    AI_GROWTH_LLM_CACHE_HOURS       default: 24
    AI_GROWTH_LOG_TOKEN_USAGE       default: false
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

try:  # Keep the app importable even before requirements are installed.
    import anthropic
except Exception:  # pragma: no cover - only hit in incomplete deployments.
    anthropic = None


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class LLMCallResult:
    data: Optional[dict]
    error: Optional[str] = None
    raw_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def claude_model_name() -> str:
    return os.environ.get("AI_GROWTH_CLAUDE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def llm_cache_hours() -> int:
    try:
        return max(0, int(os.environ.get("AI_GROWTH_LLM_CACHE_HOURS", "24")))
    except Exception:
        return 24


def llm_batch_size() -> int:
    try:
        return max(1, min(50, int(os.environ.get("AI_GROWTH_LLM_BATCH_SIZE", "20"))))
    except Exception:
        return 20


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


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _compact_value(value: Any) -> Any:
    """Remove empty payload fields before they are billed as input tokens."""
    if isinstance(value, dict):
        compacted = {str(k): _compact_value(v) for k, v in value.items()}
        return {k: v for k, v in compacted.items() if v is not None and v != "" and v != [] and v != {}}
    if isinstance(value, (list, tuple)):
        compacted = [_compact_value(v) for v in value]
        return [v for v in compacted if v is not None and v != "" and v != [] and v != {}]
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value


def _compact_json(payload: dict) -> str:
    return json.dumps(
        _compact_value(payload),
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _usage_tokens(message: Any) -> tuple[int, int]:
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


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
            max_tokens=max_tokens or _safe_int_env("AI_GROWTH_LLM_MAX_TOKENS", 1200),
            temperature=_safe_float_env("AI_GROWTH_LLM_TEMPERATURE", 0.1),
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": _compact_json(payload),
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - network/API dependent.
        return LLMCallResult(data=None, error=f"Claude API request failed: {exc}")

    text = _message_to_text(message)
    input_tokens, output_tokens = _usage_tokens(message)
    if _truthy_env("AI_GROWTH_LOG_TOKEN_USAGE"):
        print(
            "[llm-usage] "
            f"model={claude_model_name()} input={input_tokens} output={output_tokens} "
            f"task={payload.get('task', 'unknown')}"
        )
    data = _extract_json(text)
    if data is None:
        return LLMCallResult(
            data=None,
            error="Claude response was not valid JSON.",
            raw_text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return LLMCallResult(
        data=data,
        raw_text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


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
    payload = {
        "id": _get(survey, "id"),
        "title": (_get(survey, "title", "") or "")[:120],
        "description": (_get(survey, "description", "") or "")[:320],
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
    return _compact_value(payload)


def safe_user_payload(user: Any) -> dict:
    """Participant payload safe to send to Claude.

    Never send identifiers, email, passwords, OAuth IDs, payout fields, or
    sensitive profile fields. The participant_id is passed separately only as a
    local row key for JSON mapping.
    """
    created_at = _get(user, "created_at")
    account_age_days = None
    if isinstance(created_at, datetime):
        try:
            account_age_days = max(0, (datetime.utcnow() - created_at.replace(tzinfo=None)).days)
        except Exception:
            account_age_days = None

    return _compact_value({
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
        "account_age_days": account_age_days,
    })


# ---------------------------------------------------------------------------
# Prompt entry points
# ---------------------------------------------------------------------------

REASON_CODE_GUIDE = """
Reason codes only. Positive: short_task, fair_reward, device_fit, strong_history,
category_fit, clear_task, builtin_flow, active_user. Risks: long_task, low_reward,
external_friction, sparse_history, device_mismatch, unclear_task, narrow_fit.
""".strip()


PREDICT_ONE_SYSTEM = f"""
Estimate completion probability from the supplied facts only. Eligibility is already
hard-filtered. Never infer sensitive/protected traits. If evidence is sparse, lower
confidence. Return JSON only:
{{"p":0.0,"c":"l|m|h","pos":["code"],"risk":["code"]}}
p is 0..1; use at most 2 positive and 2 risk codes.
{REASON_CODE_GUIDE}
""".strip()

PREDICT_PARTICIPANTS_SYSTEM = f"""
Rank participants for one survey from supplied facts only; eligibility is already
hard-filtered. Never infer sensitive/protected traits. Return JSON only:
{{"predictions":[{{"participant_id":1,"p":0.0,"c":"l|m|h","pos":["code"],"risk":["code"]}}]}}
Return exactly one item per participant_id. p is 0..1; max 2 codes in each list.
{REASON_CODE_GUIDE}
""".strip()

RANK_SURVEYS_SYSTEM = f"""
Estimate completion likelihood for each eligible survey using supplied facts only.
Do not rank by urgency alone. Never infer sensitive/protected traits. JSON only:
{{"recommendations":[{{"survey_id":1,"p":0.0,"c":"l|m|h","pos":["code"],"risk":["code"]}}]}}
Return exactly one item per survey_id. p is 0..1; max 2 codes in each list.
{REASON_CODE_GUIDE}
""".strip()

SUMMARY_SYSTEM = """
Summarize the supplied aggregate LLM predictions for a publisher. Never infer
sensitive/protected traits. Return JSON only:
{"p":0.0,"c":"l|m|h","segment":"...","pos":["..."],"risk":["..."],"action":"...","audience":"..."}
Keep each text field under 18 words and each list to at most 3 items.
""".strip()

PREVIEW_SYSTEM = """
Estimate completion probability for this unsaved survey from aggregate pool facts
and a small representative sample. Never infer sensitive/protected traits. JSON only:
{"p":0.0,"c":"l|m|h","segment":"...","pos":["..."],"risk":["..."],"action":"...","audience":"..."}
p is 0..1. Keep text under 18 words and lists to at most 3 items.
""".strip()


def predict_one_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=PREDICT_ONE_SYSTEM, payload=payload, max_tokens=320)


def predict_participants_with_claude(payload: dict) -> LLMCallResult:
    count = len(payload.get("participants") or [])
    return call_claude_json(
        system=PREDICT_PARTICIPANTS_SYSTEM,
        payload=payload,
        max_tokens=min(1600, 180 + (count * 58)),
    )


def rank_surveys_with_claude(payload: dict) -> LLMCallResult:
    count = len(payload.get("surveys") or [])
    return call_claude_json(
        system=RANK_SURVEYS_SYSTEM,
        payload=payload,
        max_tokens=min(1600, 180 + (count * 58)),
    )


def summarize_survey_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=SUMMARY_SYSTEM, payload=payload, max_tokens=420)


def preview_survey_with_claude(payload: dict) -> LLMCallResult:
    return call_claude_json(system=PREVIEW_SYSTEM, payload=payload, max_tokens=420)
