"""Built-in response evaluation and missing-check orchestration."""
from __future__ import annotations

from app.models import Answer
from typing import List
from app.models import Question
from app.models import Response
from app.models import ResponseQualityCheck
from sqlalchemy.orm import Session
from app.models import Survey
from app.models import User

from .types import (
    QualityScoreResult,
)
from .normalization import (
    _duration_seconds_between,
)
from .features import (
    build_peer_features,
)
from .scoring import (
    compute_quality_score,
)
from .repository import (
    _check_blacklist,
    _submission_stats,
    upsert_builtin_quality_check,
)

def evaluate_builtin_response(db: Session, survey_id: int, response_id: int, *, run_llm: bool = False) -> QualityScoreResult:
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    questions = db.query(Question).filter(Question.survey_id == survey_id).all()
    question_map = {q.id: q for q in questions}
    answers = db.query(Answer).filter(Answer.response_id == response_id).all()
    answers_by_qid = {a.question_id: a.answer_value for a in answers}

    response = db.query(Response).filter(Response.id == response_id).first()
    duration_seconds = None
    participant_profile = None
    if response:
        duration_seconds = _duration_seconds_between(response.started_at, response.completed_at)
        participant = db.query(User).filter(User.id == response.participant_id).first()
        if participant:
            participant_profile = {
                "age_range": participant.age_range,
                "status": participant.status,
                "education_level": participant.education_level,
            }

    historical = []
    all_completed = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
        Response.started_at.isnot(None),
        Response.completed_at.isnot(None),
    ).all()
    for item in all_completed:
        sec = _duration_seconds_between(item.started_at, item.completed_at)
        if sec:
            historical.append(sec)

    blacklist_hits: List[str] = []
    if response:
        _, blacklist_hits = _check_blacklist(
            db,
            client_ip=response.client_ip,
            participant_id=response.participant_id,
            device_fingerprint=response.device_fingerprint,
        )
        submission_stats = _submission_stats(
            db,
            survey_id=survey_id,
            response_id=response.id,
            participant_id=response.participant_id,
            client_ip=response.client_ip,
            device_fingerprint=response.device_fingerprint,
        )
    else:
        submission_stats = {}

    peer_features = build_peer_features(db, survey_id, exclude_response_id=response_id)

    return compute_quality_score(
        question_map=question_map,
        answers_by_qid=answers_by_qid,
        duration_seconds=duration_seconds,
        historical_durations=historical,
        participant_profile=participant_profile,
        submission_stats=submission_stats,
        blacklist_hits=blacklist_hits,
        peer_features=peer_features,
        survey_title=survey.title if survey else "",
        survey_description=survey.description if survey else "",
        survey_reward=float(survey.reward_amount if survey else 0.0),
        run_llm=run_llm,
    )

def ensure_builtin_quality_checks(db: Session, survey_id: int) -> int:
    """Score completed built-in responses that do not have a quality row yet."""
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey or survey.form_url != "__builtin__":
        return 0

    completed = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
    ).all()
    created = 0
    for resp in completed:
        existing = db.query(ResponseQualityCheck).filter(
            ResponseQualityCheck.response_id == resp.id
        ).first()
        if existing:
            continue
        result = evaluate_builtin_response(db, survey_id=survey_id, response_id=resp.id, run_llm=False)
        upsert_builtin_quality_check(db, survey_id=survey_id, response_id=resp.id, result=result)
        created += 1
    if created:
        db.commit()
    return created
