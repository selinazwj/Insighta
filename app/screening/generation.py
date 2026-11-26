"""Optional Claude wording assistance with deterministic fallback."""
from __future__ import annotations

from typing import Any
import json
import os

from .drafting import (
    deterministic_draft,
)
from .validation import (
    validate_ai_draft,
)

def generate_screening_draft(survey: Any, *, use_ai: bool = False) -> list[dict[str, Any]]:
    """
    Generate a reviewable draft.

    Without a configured AI key this returns the deterministic baseline. The AI
    is constrained to rewriting that baseline and cannot invent new criteria.
    """
    baseline = deterministic_draft(survey)
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not (use_ai and api_key and baseline):
        return baseline

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=(os.environ.get("SCREENING_AI_MODEL") or "claude-haiku-4-5-20251001").strip(),
            max_tokens=1600,
            messages=[{
                "role": "user",
                "content": (
                    "Rewrite the following deterministic eligibility questions so they are clear, "
                    "neutral, and difficult to answer accidentally. Return only a JSON array. "
                    "Keep every attribute, question_type, and accepted_answers exactly unchanged. "
                    "Questions whose question_type is single_context are low-sensitivity context "
                    "checks: do not imply that one answer controls eligibility. You may add "
                    "plausible non-deceptive options. Do not ask for ID numbers, exact date of "
                    "birth, biometrics, diagnoses, medical records, or other sensitive data. "
                    "These questions test study eligibility, not whether someone is human.\n\n"
                    + json.dumps(baseline, ensure_ascii=False)
                ),
            }],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].lstrip()
        return validate_ai_draft(json.loads(raw), baseline)
    except Exception:
        return baseline
