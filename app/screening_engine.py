"""Low-sensitivity, study-specific eligibility screening.

AI may rewrite a researcher-approved rule set into clearer randomized questions,
but it is never used as proof that a participant is human and it never decides
eligibility from an unconstrained free-text answer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import secrets
from typing import Any, Iterable, Mapping, Optional


PROHIBITED_PROMPT_TERMS = frozenset({
    "social security",
    "ssn",
    "passport number",
    "driver's license number",
    "government id",
    "exact date of birth",
    "full legal name",
    "home address",
    "phone number",
    "email address",
    "credit card",
    "bank account",
    "medical record",
    "diagnosis",
    "sexual orientation",
    "biometric",
    "selfie",
    "身份证",
    "护照号码",
    "驾照号码",
    "出生日期",
    "家庭住址",
    "电话号码",
    "邮箱地址",
    "银行卡",
    "病历",
    "诊断",
    "性取向",
    "生物识别",
    "自拍",
})

EDITABLE_ATTRIBUTES = frozenset({
    "occupation",
    "student_enrollment",
    "student_program_area",
    "student_course_load",
    "student_status",
    "year_in_school",
    "participation_format",
    "device",
    "language",
    "state",
})

OCCUPATION_LABELS = {
    "physician": "Physician",
    "nurse": "Nurse",
    "lawyer": "Lawyer",
    "therapist": "Therapist",
    "engineer": "Engineer",
    "student": "Student",
    "teacher": "Teacher",
    "it_professional": "IT Professional",
    "researcher": "Researcher",
    "marketer": "Marketer",
    "business": "Business",
    "general_public": "General Public",
    "other": "Other",
}


@dataclass(frozen=True)
class ScreeningDecision:
    status: str  # passed | failed | review
    risk_score: int
    reasons: tuple[str, ...]


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("|", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = str(raw or "").strip()
        normalized = item.casefold()
        if item and normalized not in {"all", "any", "both"} and normalized not in seen:
            out.append(item)
            seen.add(normalized)
    return out


def _question(
    *,
    attribute: str,
    prompt: str,
    accepted: Iterable[str],
    distractors: Iterable[str],
    rationale: str,
    question_type: str = "single",
    all_answers_are_context: bool = False,
) -> dict[str, Any]:
    accepted_values = _split_values(list(accepted))
    options: list[str] = []
    max_distractors = max(0, 7 - len(accepted_values))
    values = [
        *accepted_values,
        *_split_values(list(distractors))[:max_distractors],
        "Prefer not to say",
    ]
    seen_options: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if value and normalized not in seen_options:
            options.append(value)
            seen_options.add(normalized)
    secrets.SystemRandom().shuffle(options)
    if all_answers_are_context:
        accepted_values = list(options)
    return {
        "attribute": attribute,
        "prompt": prompt,
        "question_type": question_type,
        "options": options[:8],
        "accepted_answers": accepted_values,
        "rationale": rationale,
        "sensitivity": "low",
        "source": "rules",
    }


def deterministic_draft(survey: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    """Create auditable questions from non-sensitive structured targeting fields."""
    required_occupations = _split_values(getattr(survey, "required_occupation", None))
    student_only = (
        len(required_occupations) == 1
        and required_occupations[0].casefold() == "student"
    )

    questions: list[dict[str, Any]] = []
    if student_only:
        questions.extend([
            _question(
                attribute="student_enrollment",
                prompt="Which statement best describes your current enrollment?",
                accepted=[
                    "Currently enrolled in a degree, diploma, or certificate program (including a scheduled break between terms)",
                ],
                distractors=[
                    "Accepted to a program, but have not started yet",
                    "Taking individual courses without being enrolled in a program",
                    "Previously enrolled, but not currently",
                    "Not currently enrolled",
                ],
                rationale="Confirms the study's Student requirement without asking the participant to select an occupation label.",
            ),
            _question(
                attribute="student_program_area",
                prompt="What broad area best matches your current program or major?",
                accepted=[],
                distractors=[
                    "Arts and humanities",
                    "Business and economics",
                    "Education",
                    "Engineering, computing, or technology",
                    "Health or life sciences",
                    "Social or behavioral sciences",
                    "Interdisciplinary, undecided, or another area",
                ],
                rationale="Collects a low-sensitivity student-context answer; no single option determines eligibility.",
                question_type="single_context",
                all_answers_are_context=True,
            ),
            _question(
                attribute="student_course_load",
                prompt="During your current or most recent academic term, what best describes your course load?",
                accepted=[],
                distractors=[
                    "1 course or fewer than 6 credits/units",
                    "2 courses or about 6–8 credits/units",
                    "3 courses or about 9–11 credits/units",
                    "4 or more courses or 12+ credits/units",
                    "Research, thesis, dissertation, co-op, or placement",
                    "My institution uses a different course-load system",
                    "Not sure",
                ],
                rationale="Collects a low-sensitivity course-load context answer without assuming every institution uses the same credit system.",
                question_type="single_context",
                all_answers_are_context=True,
            ),
        ])

    specs = [
        (
            "occupation",
            "required_occupation",
            "Which option best describes your current occupation?",
            ["Student", "Teacher", "Healthcare", "Technology", "Research", "Business", "Other"],
        ),
        (
            "student_status",
            "target_student_status",
            "What is your current student status?",
            ["Full-time student", "Part-time student", "Not currently a student"],
        ),
        (
            "year_in_school",
            "target_year_in_school",
            "What is your current year of study?",
            ["First year", "Second year", "Third year", "Fourth year", "Graduate student"],
        ),
        (
            "participation_format",
            "target_participation_format",
            "Which participation format can you currently use?",
            ["Online surveys", "Phone interview", "Video interview", "In-person study", "Any format"],
        ),
        (
            "device",
            "target_device",
            "Which device can you use for this study?",
            ["Desktop or laptop", "Mobile phone", "Tablet", "Any device"],
        ),
        (
            "language",
            "target_language",
            "Which language can you comfortably use for this study?",
            ["English", "Spanish", "Mandarin Chinese", "French", "Other"],
        ),
        (
            "state",
            "target_state",
            "Which state do you currently live in?",
            ["Massachusetts", "California", "New York", "Texas", "Florida", "Other"],
        ),
    ]
    for attribute, model_field, prompt, distractors in specs:
        accepted = _split_values(getattr(survey, model_field, None))
        if not accepted:
            continue
        if attribute == "occupation" and student_only:
            continue
        if attribute == "occupation":
            accepted = [
                OCCUPATION_LABELS.get(value.casefold(), value)
                for value in accepted
            ]
        questions.append(_question(
            attribute=attribute,
            prompt=prompt,
            accepted=accepted,
            distractors=distractors,
            rationale=f"Checks the study's {attribute.replace('_', ' ')} requirement.",
        ))
        if len(questions) >= limit:
            break
    return questions[:limit]


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


def evaluate_screening(
    questions: Iterable[Mapping[str, Any]],
    answers: Mapping[str, Any],
) -> ScreeningDecision:
    """Deterministically evaluate an approved, versioned question set."""
    reasons: list[str] = []
    missing = 0
    failed = 0
    for question in questions:
        attribute = str(question.get("attribute") or "")
        answer = str(answers.get(attribute) or "").strip()
        question_type = str(question.get("question_type") or "single")
        accepted = {value.lower() for value in _split_values(question.get("accepted_answers"))}
        if not answer:
            missing += 1
            reasons.append(f"Missing answer for {attribute}")
        elif question_type == "single_context":
            continue
        elif accepted and answer.lower() not in accepted:
            failed += 1
            reasons.append(f"Does not meet the {attribute.replace('_', ' ')} requirement")

    if missing:
        return ScreeningDecision(
            status="review",
            risk_score=min(100, 25 + missing * 15),
            reasons=tuple(reasons),
        )
    if failed:
        return ScreeningDecision(
            status="failed",
            risk_score=min(100, 20 + failed * 20),
            reasons=tuple(reasons),
        )
    return ScreeningDecision(status="passed", risk_score=0, reasons=())
