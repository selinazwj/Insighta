"""Composition of rule, anomaly, and optional semantic scores."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from app.models import Question
from typing import Tuple

from .types import (
    QualityScoreResult,
)
from .normalization import (
    _label_from_score,
    _review_status_from_label,
)
from .features import (
    _extract_response_features,
)
from .anomaly import (
    _isolation_forest_anomaly_score,
)
from .rules import (
    _compute_rule_penalty,
)
from .semantic import (
    _run_llm_semantic_eval,
    _should_run_llm,
)

def compute_quality_score(
    *,
    question_map: Dict[int, Question],
    answers_by_qid: Dict[int, Any],
    duration_seconds: Optional[float],
    historical_durations: Optional[Iterable[float]] = None,
    participant_profile: Optional[Dict[str, Any]] = None,
    submission_stats: Optional[Dict[str, int]] = None,
    blacklist_hits: Optional[List[str]] = None,
    peer_features: Optional[List[Dict[str, float]]] = None,
    survey_title: str = "",
    survey_description: str = "",
    survey_reward: float = 0.0,
    run_llm: bool = False,
    force_llm: bool = False,
    precomputed_anomaly: Optional[Tuple[float, List[str]]] = None,
) -> QualityScoreResult:
    penalty, triggered_rules, reasons, missing_required = _compute_rule_penalty(
        question_map=question_map,
        answers_by_qid=answers_by_qid,
        duration_seconds=duration_seconds,
        historical_durations=historical_durations,
        participant_profile=participant_profile,
        submission_stats=submission_stats,
        blacklist_hits=blacklist_hits,
    )

    features = _extract_response_features(
        question_map=question_map,
        answers_by_qid=answers_by_qid,
        duration_seconds=duration_seconds,
        missing_required=missing_required,
    )
    if precomputed_anomaly is not None:
        anomaly_score, anomaly_reasons = precomputed_anomaly
    else:
        anomaly_score, anomaly_reasons = _isolation_forest_anomaly_score(features, peer_features or [])
    if anomaly_score > 0:
        triggered_rules.append({
            "rule_id": "statistical_anomaly",
            "triggered": True,
            "penalty": anomaly_score,
            "reason": "Statistical anomaly detected",
        })
        reasons.extend(anomaly_reasons)

    preliminary_score = max(0.0, 100.0 - penalty - anomaly_score)
    semantic_risk = 0.0
    llm_result_json = None

    should_run_llm = force_llm or _should_run_llm(
        question_map=question_map,
        rule_penalty=penalty,
        anomaly_score=anomaly_score,
        preliminary_score=preliminary_score,
        survey_reward=survey_reward,
    )
    if run_llm and should_run_llm:
        semantic_risk, llm_result_json, llm_reasons = _run_llm_semantic_eval(
            survey_title=survey_title,
            survey_description=survey_description,
            question_map=question_map,
            answers_by_qid=answers_by_qid,
        )
        if semantic_risk > 0:
            triggered_rules.append({
                "rule_id": "llm_semantic_risk",
                "triggered": True,
                "penalty": semantic_risk,
                "reason": "LLM semantic quality risk",
            })
            reasons.extend(llm_reasons)

    total_deduction = min(95.0, penalty + anomaly_score + semantic_risk)
    quality_score = max(0.0, 100.0 - total_deduction)
    fraud_risk = penalty >= 60.0 or missing_required >= 3 or bool(blacklist_hits)

    if not reasons:
        reasons.append("Complete response with no obvious quality issues")

    label = _label_from_score(quality_score, fraud_risk)
    return QualityScoreResult(
        quality_score=round(quality_score, 2),
        quality_label=label,
        fraud_risk=fraud_risk,
        rule_penalty=round(penalty, 2),
        anomaly_score=round(anomaly_score, 2),
        semantic_risk=round(semantic_risk, 2),
        triggered_rules=triggered_rules,
        reasons=reasons,
        llm_result_json=llm_result_json,
        review_status=_review_status_from_label(label, fraud_risk),
        metadata={
            "features": features,
            "submission_stats": submission_stats or {},
        },
    )
