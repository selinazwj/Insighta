"""Eligibility-preserving validation of AI and researcher edits."""
from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import Mapping

from .constants import (
    EDITABLE_ATTRIBUTES,
    PROHIBITED_PROMPT_TERMS,
)
from .normalization import (
    _split_values,
)

def _contains_prohibited_term(prompt: str) -> bool:
    normalized = (prompt or "").strip().lower()
    return any(term in normalized for term in PROHIBITED_PROMPT_TERMS)

def validate_ai_draft(
    candidate: Any,
    baseline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Accept AI wording only when it preserves the deterministic rule contract.

    Invalid, sensitive, or hallucinated questions are silently replaced by the
    baseline rule question for that attribute.
    """
    baseline_by_attribute = {item["attribute"]: item for item in baseline}
    candidate_by_attribute = {
        str(item.get("attribute") or ""): item
        for item in candidate
        if isinstance(item, dict)
    } if isinstance(candidate, list) else {}

    validated: list[dict[str, Any]] = []
    for attribute, fallback in baseline_by_attribute.items():
        item = candidate_by_attribute.get(attribute)
        if not item:
            validated.append(fallback)
            continue
        prompt = str(item.get("prompt") or "").strip()
        options = _split_values(item.get("options"))
        accepted = _split_values(item.get("accepted_answers"))
        expected = _split_values(fallback["accepted_answers"])
        valid = (
            bool(prompt)
            and not _contains_prohibited_term(prompt)
            and 2 <= len(options) <= 8
            and {value.lower() for value in accepted} == {value.lower() for value in expected}
            and all(any(value.lower() == option.lower() for option in options) for value in accepted)
        )
        if not valid:
            validated.append(fallback)
            continue
        validated.append({
            **fallback,
            "prompt": prompt,
            "options": options,
            "accepted_answers": accepted,
            "rationale": str(item.get("rationale") or fallback["rationale"]).strip(),
            "source": "ai_assisted",
        })
    return validated

def validate_researcher_edit(
    candidate: Any,
    baseline: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate wording/options edits without letting eligibility rules drift."""
    baseline_rows = [dict(item) for item in baseline]
    baseline_by_attribute = {
        str(item.get("attribute") or ""): item for item in baseline_rows
    }
    if not isinstance(candidate, list) or not candidate:
        raise ValueError("Add at least one screening question.")
    if len(candidate) != len(baseline_rows):
        raise ValueError("Questions cannot be added or removed from this rule-based draft.")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for order_index, item in enumerate(candidate):
        if not isinstance(item, dict):
            raise ValueError("Every screening question must be an object.")
        attribute = str(item.get("attribute") or "").strip()
        baseline_item = baseline_by_attribute.get(attribute)
        if (
            attribute not in EDITABLE_ATTRIBUTES
            or not baseline_item
            or attribute in seen
        ):
            raise ValueError("Screening attributes cannot be added, duplicated, or changed.")
        seen.add(attribute)

        prompt = str(item.get("prompt") or "").strip()
        if not prompt or len(prompt) > 240:
            raise ValueError("Each question needs a prompt of 240 characters or fewer.")
        if _contains_prohibited_term(prompt):
            raise ValueError("That wording asks for information this screener cannot collect.")

        options = _split_values(item.get("options"))
        if not 2 <= len(options) <= 8:
            raise ValueError("Each question must have between 2 and 8 answer options.")
        expected = _split_values(baseline_item.get("accepted_answers"))
        submitted_accepted = _split_values(item.get("accepted_answers"))
        if {
            value.casefold() for value in submitted_accepted
        } != {
            value.casefold() for value in expected
        }:
            raise ValueError("Qualifying answers cannot be changed in the wording editor.")
        if not all(
            any(value.casefold() == option.casefold() for option in options)
            for value in expected
        ):
            raise ValueError("Every qualifying answer must remain one of the options.")

        validated.append({
            **baseline_item,
            "attribute": attribute,
            "prompt": prompt,
            "options": options,
            "accepted_answers": expected,
            "order_index": order_index,
            "source": "researcher_edited",
            "sensitivity": "low",
        })
    return validated
