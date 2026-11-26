"""Spreadsheet row interpretation and quality scoring."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from app.models import Question
from typing import Tuple

from .constants import (
    EXCEL_METADATA_HEADERS,
)
from .types import (
    QualityScoreResult,
    _PseudoQuestion,
)
from .normalization import (
    _is_empty,
)
from .features import (
    _extract_response_features,
)
from .scoring import (
    compute_quality_score,
)

def _normalize_excel_header(value: Any) -> str:
    return str(value or "").strip().lower()

def _is_excel_metadata_header(header: str) -> bool:
    norm = _normalize_excel_header(header)
    if not norm:
        return True
    if norm in EXCEL_METADATA_HEADERS:
        return True
    return norm.startswith("unnamed:")

def build_pseudo_question_map_from_row(row_dict: Dict[str, Any]) -> Tuple[Dict[int, Question], Dict[int, Any]]:
    question_map: Dict[int, Question] = {}
    answers_by_qid: Dict[int, Any] = {}
    idx = 0
    for header, value in row_dict.items():
        if _is_excel_metadata_header(header):
            continue
        if _is_empty(value):
            continue
        idx += 1
        pseudo = _PseudoQuestion(
            id=idx,
            survey_id=0,
            question_text=str(header),
            question_type="text",
            is_required=False,
            order_index=idx,
        )
        question_map[idx] = pseudo  # type: ignore[assignment]
        answers_by_qid[idx] = value
    return question_map, answers_by_qid

def resolve_excel_row_context(
    *,
    row_dict: Dict[str, Any],
    mapped_question_map: Dict[int, Question],
    mapped_answers: Dict[int, Any],
    duration_seconds: Optional[float],
) -> Tuple[Dict[int, Question], Dict[int, Any], Dict[str, float]]:
    question_map = mapped_question_map
    answers_by_qid = mapped_answers
    if not answers_by_qid:
        question_map, answers_by_qid = build_pseudo_question_map_from_row(row_dict)

    missing_required = sum(
        1 for qid, q in question_map.items()
        if q.is_required and _is_empty(answers_by_qid.get(qid))
    )
    features = _extract_response_features(
        question_map=question_map,
        answers_by_qid=answers_by_qid,
        duration_seconds=duration_seconds,
        missing_required=missing_required,
    )
    return question_map, answers_by_qid, features

def compute_excel_row_quality(
    *,
    row_dict: Dict[str, Any],
    mapped_question_map: Dict[int, Question],
    mapped_answers: Dict[int, Any],
    duration_seconds: Optional[float],
    historical_durations: Optional[Iterable[float]] = None,
    peer_features: Optional[List[Dict[str, float]]] = None,
    survey_title: str = "",
    survey_description: str = "",
    survey_reward: float = 0.0,
    run_llm: bool = False,
    precomputed_anomaly: Optional[Tuple[float, List[str]]] = None,
) -> QualityScoreResult:
    question_map = mapped_question_map
    answers_by_qid = mapped_answers
    if not answers_by_qid:
        question_map, answers_by_qid = build_pseudo_question_map_from_row(row_dict)

    return compute_quality_score(
        question_map=question_map,
        answers_by_qid=answers_by_qid,
        duration_seconds=duration_seconds,
        historical_durations=historical_durations,
        peer_features=peer_features,
        survey_title=survey_title,
        survey_description=survey_description,
        survey_reward=survey_reward,
        run_llm=run_llm,
        force_llm=run_llm,
        precomputed_anomaly=precomputed_anomaly,
    )
