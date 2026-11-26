"""Quality persistence, submission history, and review updates."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from app.models import QualityBlacklist
from app.models import Response
from app.models import ResponseQualityCheck
from sqlalchemy.orm import Session
from typing import Tuple
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from .constants import (
    BULK_SUBMIT_WINDOW_HOURS,
)
from .types import (
    QualityScoreResult,
)

def _check_blacklist(db: Session, *, client_ip: Optional[str], participant_id: Optional[int], device_fingerprint: Optional[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    checks = []
    if client_ip:
        checks.append(("ip", client_ip))
    if participant_id is not None:
        checks.append(("user", str(participant_id)))
    if device_fingerprint:
        checks.append(("device", device_fingerprint))

    for block_type, block_value in checks:
        row = db.query(QualityBlacklist).filter(
            QualityBlacklist.block_type == block_type,
            QualityBlacklist.block_value == block_value,
        ).first()
        if row:
            hits.append(f"{block_type}:{block_value}")

    return bool(hits), hits

def _submission_stats(
    db: Session,
    *,
    survey_id: int,
    response_id: Optional[int],
    participant_id: Optional[int],
    client_ip: Optional[str],
    device_fingerprint: Optional[str],
) -> Dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(hours=BULK_SUBMIT_WINDOW_HOURS)
    base = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= since,
    )
    if response_id:
        base = base.filter(Response.id != response_id)

    participant_count = 0
    ip_count = 0
    device_count = 0

    if participant_id:
        participant_count = base.filter(Response.participant_id == participant_id).count()
    if client_ip:
        ip_count = base.filter(Response.client_ip == client_ip).count()
    if device_fingerprint:
        device_count = base.filter(Response.device_fingerprint == device_fingerprint).count()

    return {
        "participant_count_24h": participant_count,
        "ip_count_24h": ip_count,
        "device_count_24h": device_count,
    }

def apply_auto_approve_checks(
    rows: Iterable[ResponseQualityCheck],
    min_score: float,
) -> tuple:
    """Approve rows at or above min_score; reject below. High-score fraud_risk stays pending."""
    threshold = min(100.0, max(0.0, float(min_score)))
    approved = 0
    rejected = 0
    for row in rows:
        score = row.quality_score or 0.0
        if row.fraud_risk:
            if score < threshold and row.review_status != "rejected":
                row.review_status = "rejected"
                row.reviewer_label = "auto_filter"
                rejected += 1
            continue
        if score >= threshold:
            if row.review_status != "approved":
                row.review_status = "approved"
                row.reviewer_label = "auto_filter"
                approved += 1
        elif row.review_status != "rejected":
            row.review_status = "rejected"
            row.reviewer_label = "auto_filter"
            rejected += 1
    return approved, rejected

def upsert_builtin_quality_check(db: Session, survey_id: int, response_id: int, result: QualityScoreResult) -> ResponseQualityCheck:
    row = db.query(ResponseQualityCheck).filter(ResponseQualityCheck.response_id == response_id).first()
    if not row:
        row = ResponseQualityCheck(
            response_id=response_id,
            survey_id=survey_id,
            source_type="builtin",
        )
        db.add(row)

    row.quality_score = result.quality_score
    row.quality_label = result.quality_label
    row.fraud_risk = result.fraud_risk
    row.rule_penalty = result.rule_penalty
    row.anomaly_score = result.anomaly_score
    row.semantic_risk = result.semantic_risk
    row.triggered_rules = result.triggered_rules
    row.reasons = result.reasons
    row.llm_result_json = result.llm_result_json
    row.review_status = result.review_status
    return row

def create_excel_quality_check(
    db: Session,
    *,
    survey_id: int,
    source_ref: str,
    raw_response_json: Dict[str, Any],
    result: QualityScoreResult,
) -> ResponseQualityCheck:
    row = ResponseQualityCheck(
        response_id=None,
        survey_id=survey_id,
        source_type="excel",
        source_ref=source_ref,
        quality_score=result.quality_score,
        quality_label=result.quality_label,
        fraud_risk=result.fraud_risk,
        rule_penalty=result.rule_penalty,
        anomaly_score=result.anomaly_score,
        semantic_risk=result.semantic_risk,
        triggered_rules=result.triggered_rules,
        reasons=result.reasons,
        llm_result_json=result.llm_result_json,
        review_status=result.review_status,
        raw_response_json=raw_response_json,
    )
    db.add(row)
    return row
