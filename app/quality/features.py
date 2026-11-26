"""Response feature extraction and peer feature collection."""
from __future__ import annotations

from app.models import Answer
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from app.models import Question
from app.models import Response
from sqlalchemy.orm import Session

from .constants import (
    LOW_QUALITY_TEXT_TERMS,
)
from .normalization import (
    _answer_text,
    _duration_seconds_between,
    _is_empty,
    _normalize_text,
    _safe_float,
)

def _extract_response_features(
    *,
    question_map: Dict[int, Question],
    answers_by_qid: Dict[int, Any],
    duration_seconds: Optional[float],
    missing_required: int,
) -> Dict[str, float]:
    text_lengths: List[float] = []
    scale_values: List[float] = []
    text_total = 0
    low_quality_text = 0

    for qid, question in question_map.items():
        if question.question_type == "text":
            text_total += 1
            raw = _answer_text(answers_by_qid.get(qid))
            text_lengths.append(float(len(raw)))
            norm = _normalize_text(raw)
            if raw and (norm in LOW_QUALITY_TEXT_TERMS or (len(norm) < 5 and not norm.isdigit())):
                low_quality_text += 1
        elif question.question_type == "scale":
            val = _safe_float(answers_by_qid.get(qid))
            if val is not None:
                scale_values.append(val)

    scale_same_ratio = 0.0
    if len(scale_values) >= 2:
        scale_same_ratio = max(scale_values.count(v) for v in set(scale_values)) / len(scale_values)

    required_total = sum(1 for q in question_map.values() if q.is_required) or 1
    return {
        "duration_seconds": float(duration_seconds or 0.0),
        "text_avg_length": float(sum(text_lengths) / len(text_lengths)) if text_lengths else 0.0,
        "text_low_quality_ratio": float(low_quality_text / text_total) if text_total else 0.0,
        "scale_same_ratio": scale_same_ratio,
        "missing_required_ratio": float(missing_required / required_total),
    }

def build_peer_features(db: Session, survey_id: int, exclude_response_id: Optional[int]) -> List[Dict[str, float]]:
    questions = db.query(Question).filter(Question.survey_id == survey_id).all()
    question_map = {q.id: q for q in questions}
    responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
    ).all()

    rows: List[Dict[str, float]] = []
    for resp in responses:
        if exclude_response_id and resp.id == exclude_response_id:
            continue
        answers = db.query(Answer).filter(Answer.response_id == resp.id).all()
        answers_by_qid = {a.question_id: a.answer_value for a in answers}
        duration_seconds = None
        duration_seconds = _duration_seconds_between(resp.started_at, resp.completed_at)
        missing_required = sum(
            1 for qid, q in question_map.items()
            if q.is_required and _is_empty(answers_by_qid.get(qid))
        )
        rows.append(_extract_response_features(
            question_map=question_map,
            answers_by_qid=answers_by_qid,
            duration_seconds=duration_seconds,
            missing_required=missing_required,
        ))
    return rows
