"""Deterministic question generation from study eligibility fields."""
from __future__ import annotations

from typing import Any
from typing import Iterable
import secrets

from .constants import (
    OCCUPATION_LABELS,
)
from .normalization import (
    _split_values,
)

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
