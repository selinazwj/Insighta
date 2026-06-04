from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import Answer, QualityBlacklist, Question, Response, ResponseQualityCheck, Survey, User


LOW_QUALITY_TEXT_TERMS = {
    "test",
    "n/a",
    "na",
    "none",
    "idk",
    "dont know",
    "don't know",
    "dunno",
    "no idea",
    "whatever",
    "nah",
    "nothing",
    "skip",
    "asdf",
    "xxx",
}

NEGATIVE_ANSWERS = {
    "no",
    "none",
    "n/a",
    "na",
    "no car",
    "don't have a car",
    "do not have a car",
    "i don't have a car",
    "not applicable",
}

LOGIC_CONFLICT_RULES = [
    {
        "rule_id": "no_car_but_car_related",
        "negative_q_keywords": ["own a car", "have a car", "do you drive", "vehicle owner"],
        "negative_answers": NEGATIVE_ANSWERS,
        "conflict_q_keywords": [
            "car insurance",
            "license plate",
            "vehicle maintenance",
            "gas station",
            "parking",
        ],
    },
    {
        "rule_id": "student_but_retired",
        "negative_q_keywords": ["employment status", "occupation", "job status", "work status"],
        "negative_answers": {"student", "full-time student", "part-time student", "in school"},
        "conflict_q_keywords": ["retired", "retirement", "pension"],
    },
]

BULK_SUBMIT_WINDOW_HOURS = 24
BULK_SUBMIT_THRESHOLD = 3
IP_BULK_THRESHOLD = 5

EXCEL_METADATA_HEADERS = {
    "timestamp",
    "time",
    "submitted at",
    "submission time",
    "date",
    "email",
    "email address",
    "username",
    "name",
    "duration",
    "duration_seconds",
    "time_spent",
    "completion_seconds",
}


@dataclass
class _PseudoQuestion:
    id: int
    survey_id: int
    question_text: str
    question_type: str
    is_required: bool
    order_index: int


@dataclass
class QualityScoreResult:
    quality_score: float
    quality_label: str
    fraud_risk: bool
    rule_penalty: float
    anomaly_score: float
    semantic_risk: float
    triggered_rules: List[Dict[str, Any]]
    reasons: List[str]
    llm_result_json: Optional[Dict[str, Any]] = None
    review_status: str = "pending"
    metadata: Dict[str, Any] = field(default_factory=dict)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if not start or not end:
        return None
    start_dt = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_dt = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)
    seconds = (end_dt - start_dt).total_seconds()
    return seconds if seconds > 0 else None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _answer_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value).strip()


def _label_from_score(score: float, fraud_risk: bool) -> str:
    if fraud_risk:
        return "fraud_risk"
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _review_status_from_label(label: str, fraud_risk: bool) -> str:
    if fraud_risk:
        return "needs_review"
    if label == "medium":
        return "needs_review"
    if label == "low":
        return "needs_review"
    return "pending"


def _question_matches_keywords(question_text: str, keywords: Iterable[str]) -> bool:
    text = question_text.lower()
    return any(kw.lower() in text for kw in keywords)


def _answer_matches_patterns(answer_value: Any, patterns: Iterable[str]) -> bool:
    text = _normalize_text(_answer_text(answer_value))
    if not text:
        return False
    pattern_set = {p.lower() for p in patterns if p}
    if text in pattern_set:
        return True
    for pattern in pattern_set:
        if len(pattern) > 3 and pattern in text:
            return True
    return False


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


def _heuristic_anomaly_score(features: Dict[str, float], peer_features: List[Dict[str, float]]) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    score = 0.0

    if features.get("duration_seconds", 0) > 0 and peer_features:
        peer_durations = [f["duration_seconds"] for f in peer_features if f.get("duration_seconds", 0) > 0]
        if peer_durations:
            med = median(peer_durations)
            if med > 0 and features["duration_seconds"] < med * 0.15:
                score += 8.0
                reasons.append("Completion time is unusually short vs. peer responses")

    if features.get("text_low_quality_ratio", 0) >= 0.5:
        score += 6.0
        reasons.append("High ratio of low-quality open-text answers")

    if features.get("scale_same_ratio", 0) >= 0.85:
        score += 5.0
        reasons.append("Scale responses are unusually uniform")

    if features.get("missing_required_ratio", 0) > 0:
        score += min(10.0, features["missing_required_ratio"] * 20.0)
        reasons.append("Abnormally high rate of missing required answers")

    return min(30.0, score), reasons


def _isolation_forest_anomaly_score(
    features: Dict[str, float],
    peer_features: List[Dict[str, float]],
) -> Tuple[float, List[str]]:
    if len(peer_features) < 8:
        return _heuristic_anomaly_score(features, peer_features)

    try:
        from sklearn.ensemble import IsolationForest
        import numpy as np
    except ImportError:
        return _heuristic_anomaly_score(features, peer_features)

    keys = ["duration_seconds", "text_avg_length", "text_low_quality_ratio", "scale_same_ratio", "missing_required_ratio"]
    matrix = [ [float(row.get(k, 0.0)) for k in keys] for row in peer_features ]
    matrix.append([float(features.get(k, 0.0)) for k in keys])

    model = IsolationForest(contamination=0.12, random_state=42)
    model.fit(matrix)
    raw = float(model.decision_function([matrix[-1]])[0])
    is_anomaly = model.predict([matrix[-1]])[0] == -1

    if not is_anomaly:
        return 0.0, []

    # decision_function: higher = more normal; lower/negative = more anomalous
    anomaly_score = min(30.0, max(5.0, (0.5 - raw) * 20.0))
    return round(anomaly_score, 2), ["Statistical model flagged this response as an outlier vs. peer responses"]


def batch_anomaly_scores_for_features(
    all_features: List[Dict[str, float]],
) -> List[Tuple[float, List[str]]]:
    """Score anomaly for many rows at once (one model fit instead of per-row)."""
    if not all_features:
        return []

    anomaly_reason = ["Statistical model flagged this response as an outlier vs. peer responses"]

    if len(all_features) < 9:
        return [
            _heuristic_anomaly_score(
                row,
                [feat for idx, feat in enumerate(all_features) if idx != row_idx],
            )
            for row_idx, row in enumerate(all_features)
        ]

    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return [
            _heuristic_anomaly_score(
                row,
                [feat for idx, feat in enumerate(all_features) if idx != row_idx],
            )
            for row_idx, row in enumerate(all_features)
        ]

    keys = [
        "duration_seconds",
        "text_avg_length",
        "text_low_quality_ratio",
        "scale_same_ratio",
        "missing_required_ratio",
    ]
    matrix = [[float(row.get(k, 0.0)) for k in keys] for row in all_features]

    model = IsolationForest(contamination=0.12, random_state=42)
    model.fit(matrix)
    preds = model.predict(matrix)
    raw_scores = model.decision_function(matrix)

    results: List[Tuple[float, List[str]]] = []
    for idx, raw in enumerate(raw_scores):
        if preds[idx] != -1:
            results.append((0.0, []))
            continue
        anomaly_score = min(30.0, max(5.0, (0.5 - float(raw)) * 20.0))
        results.append((round(anomaly_score, 2), list(anomaly_reason)))
    return results


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


def _compute_rule_penalty(
    *,
    question_map: Dict[int, Question],
    answers_by_qid: Dict[int, Any],
    duration_seconds: Optional[float],
    historical_durations: Optional[Iterable[float]] = None,
    participant_profile: Optional[Dict[str, Any]] = None,
    submission_stats: Optional[Dict[str, int]] = None,
    blacklist_hits: Optional[List[str]] = None,
) -> Tuple[float, List[Dict[str, Any]], List[str], int]:
    triggered_rules: List[Dict[str, Any]] = []
    reasons: List[str] = []
    penalty = 0.0
    missing_required = 0

    if blacklist_hits:
        penalty += 40.0
        triggered_rules.append({
            "rule_id": "blacklist_hit",
            "triggered": True,
            "penalty": 40.0,
            "reason": f"Blacklist match: {', '.join(blacklist_hits)}",
        })
        reasons.append("Matched risk-control blacklist")

    for qid, question in question_map.items():
        if question.is_required and _is_empty(answers_by_qid.get(qid)):
            missing_required += 1
    if missing_required > 0:
        rule_penalty = min(50.0, missing_required * 12.0)
        penalty += rule_penalty
        triggered_rules.append({
            "rule_id": "missing_required",
            "triggered": True,
            "penalty": rule_penalty,
            "reason": f"{missing_required} required question(s) left blank",
        })
        reasons.append("Required question(s) missing")

    valid_history = [d for d in (historical_durations or []) if d and d > 0]
    if duration_seconds and valid_history:
        med = median(valid_history)
        if med > 0 and duration_seconds < med * 0.2:
            penalty += 20.0
            triggered_rules.append({
                "rule_id": "short_duration",
                "triggered": True,
                "penalty": 20.0,
                "reason": "Completion time is far below the historical median",
            })
            reasons.append("Unusually short completion time")

    low_quality_text_count = 0
    for qid, question in question_map.items():
        if question.question_type != "text":
            continue
        raw_text = _normalize_text(answers_by_qid.get(qid))
        if not raw_text:
            continue
        if raw_text in LOW_QUALITY_TEXT_TERMS:
            low_quality_text_count += 1
        elif len(raw_text) < 5 and not raw_text.isdigit():
            low_quality_text_count += 1
    if low_quality_text_count > 0:
        text_penalty = min(25.0, low_quality_text_count * 8.0)
        penalty += text_penalty
        triggered_rules.append({
            "rule_id": "low_quality_text",
            "triggered": True,
            "penalty": text_penalty,
            "reason": f"{low_quality_text_count} open-text answer(s) look like low-effort responses",
        })
        reasons.append("Open-text answers appear low quality")

    scale_values: List[float] = []
    for qid, question in question_map.items():
        if question.question_type != "scale":
            continue
        val = _safe_float(answers_by_qid.get(qid))
        if val is not None:
            scale_values.append(val)
    if len(scale_values) >= 4:
        same_ratio = max(scale_values.count(v) for v in set(scale_values)) / len(scale_values)
        if same_ratio >= 0.9:
            penalty += 15.0
            triggered_rules.append({
                "rule_id": "scale_straightlining",
                "triggered": True,
                "penalty": 15.0,
                "reason": "Scale questions show heavy straight-lining",
            })
            reasons.append("Possible straight-lining on scale questions")

    stats = submission_stats or {}
    if stats.get("participant_count_24h", 0) >= BULK_SUBMIT_THRESHOLD:
        p = 18.0
        penalty += p
        triggered_rules.append({
            "rule_id": "participant_bulk_submit",
            "triggered": True,
            "penalty": p,
            "reason": f"Same participant submitted {stats['participant_count_24h']} times in {BULK_SUBMIT_WINDOW_HOURS}h",
        })
        reasons.append("Repeated submissions from the same account")

    if stats.get("ip_count_24h", 0) >= IP_BULK_THRESHOLD:
        p = 22.0
        penalty += p
        triggered_rules.append({
            "rule_id": "ip_bulk_submit",
            "triggered": True,
            "penalty": p,
            "reason": f"Same IP submitted {stats['ip_count_24h']} times in {BULK_SUBMIT_WINDOW_HOURS}h",
        })
        reasons.append("High submission volume from the same IP")

    if stats.get("device_count_24h", 0) >= BULK_SUBMIT_THRESHOLD:
        p = 18.0
        penalty += p
        triggered_rules.append({
            "rule_id": "device_bulk_submit",
            "triggered": True,
            "penalty": p,
            "reason": f"Same device submitted {stats['device_count_24h']} times in {BULK_SUBMIT_WINDOW_HOURS}h",
        })
        reasons.append("Repeated submissions from the same device")

    for rule in LOGIC_CONFLICT_RULES:
        negative_qids = [
            qid for qid, q in question_map.items()
            if _question_matches_keywords(q.question_text, rule["negative_q_keywords"])
        ]
        conflict_qids = [
            qid for qid, q in question_map.items()
            if _question_matches_keywords(q.question_text, rule["conflict_q_keywords"])
        ]
        if not negative_qids or not conflict_qids:
            continue

        negative_hit = any(
            _answer_matches_patterns(answers_by_qid.get(qid), rule["negative_answers"])
            for qid in negative_qids
        )
        conflict_hit = any(
            not _is_empty(answers_by_qid.get(qid))
            and not _answer_matches_patterns(answers_by_qid.get(qid), rule["negative_answers"] | {"", "no", "none", "n/a"})
            for qid in conflict_qids
        )
        if negative_hit and conflict_hit:
            p = 20.0
            penalty += p
            triggered_rules.append({
                "rule_id": rule["rule_id"],
                "triggered": True,
                "penalty": p,
                "reason": "Cross-question logic conflict detected",
            })
            reasons.append("Answers appear logically inconsistent")

    if participant_profile:
        status = _normalize_text(participant_profile.get("status"))
        age_range = _normalize_text(participant_profile.get("age_range"))
        if status and "student" in status:
            for qid, question in question_map.items():
                if "retired" in question.question_text.lower():
                    ans = _normalize_text(_answer_text(answers_by_qid.get(qid)))
                    if ans and "retired" in ans:
                        p = 18.0
                        penalty += p
                        triggered_rules.append({
                            "rule_id": "profile_logic_conflict",
                            "triggered": True,
                            "penalty": p,
                            "reason": "Profile says student but response mentions retirement",
                        })
                        reasons.append("Profile and answers are logically inconsistent")
                        break
        if age_range in {"18-24", "18-22", "under 18", "under 18 years"}:
            for qid, question in question_map.items():
                qtext = question.question_text.lower()
                if any(k in qtext for k in ["years of experience", "work experience", "years employed"]):
                    match = re.search(r"\d+", _answer_text(answers_by_qid.get(qid)) or "")
                    ans = _safe_float(match.group(0)) if match else None
                    if ans is not None and ans >= 10:
                        p = 15.0
                        penalty += p
                        triggered_rules.append({
                            "rule_id": "age_experience_conflict",
                            "triggered": True,
                            "penalty": p,
                            "reason": "Young age range conflicts with long work experience",
                        })
                        reasons.append("Age and experience answers do not align")
                        break

    penalty = min(95.0, penalty)
    return penalty, triggered_rules, reasons, missing_required


def _should_run_llm(
    *,
    question_map: Dict[int, Question],
    rule_penalty: float,
    anomaly_score: float,
    preliminary_score: float,
    survey_reward: float,
) -> bool:
    text_count = sum(1 for q in question_map.values() if q.question_type == "text")
    if text_count >= 2:
        return True
    if survey_reward >= 10:
        return True
    if rule_penalty >= 15:
        return True
    if anomaly_score >= 8:
        return True
    if 50 <= preliminary_score < 80:
        return True
    return False


def get_anthropic_api_key() -> Optional[str]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return key or None


def anthropic_api_key_configured() -> bool:
    return get_anthropic_api_key() is not None


def _build_semantic_qa_pairs(
    question_map: Dict[int, Question],
    answers_by_qid: Dict[int, Any],
) -> List[Dict[str, Any]]:
    qa_pairs = []
    for qid in sorted(question_map.keys(), key=lambda i: question_map[i].order_index):
        q = question_map[qid]
        val = answers_by_qid.get(qid)
        if _is_empty(val):
            continue
        qa_pairs.append({
            "question": q.question_text,
            "type": q.question_type,
            "answer": val,
        })
    return qa_pairs


def _mock_semantic_eval(qa_pairs: List[Dict[str, Any]]) -> Tuple[float, Dict[str, Any], List[str]]:
    """Placeholder semantic scores when no LLM API key (deterministic per response body)."""
    seed_material = json.dumps(qa_pairs, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))

    semantic_risk = round(rng.uniform(0.0, 30.0), 2)
    conflict = rng.random() < 0.12
    if conflict and semantic_risk < 15:
        semantic_risk = 15.0

    parsed: Dict[str, Any] = {
        "mode": "mock",
        "semantic_relevance": rng.randint(1, 5),
        "specificity": rng.randint(1, 5),
        "clarity": rng.randint(1, 5),
        "cross_question_conflict": conflict,
        "semantic_risk": semantic_risk,
    }
    reasons: List[str] = []
    if semantic_risk >= 12:
        reasons.append("Simulated semantic quality review")
    if conflict:
        reasons.append("Simulated cross-question inconsistency flag")
    return semantic_risk, parsed, reasons


def _run_llm_semantic_eval(
    *,
    survey_title: str,
    survey_description: str,
    question_map: Dict[int, Question],
    answers_by_qid: Dict[int, Any],
) -> Tuple[float, Optional[Dict[str, Any]], List[str]]:
    qa_pairs = _build_semantic_qa_pairs(question_map, answers_by_qid)
    if not qa_pairs:
        return 0.0, None, []

    api_key = get_anthropic_api_key()
    if not api_key:
        risk, parsed, reasons = _mock_semantic_eval(qa_pairs)
        return risk, parsed, reasons

    prompt = f"""You are a survey quality reviewer. Evaluate the response quality below and return JSON only (no markdown).

Survey title: {survey_title}
Survey description: {survey_description or 'N/A'}

Response data:
{json.dumps(qa_pairs, ensure_ascii=False, indent=2)}

Return JSON with these fields:
{{
  "semantic_relevance": 1-5,
  "specificity": 1-5,
  "clarity": 1-5,
  "cross_question_conflict": true/false,
  "semantic_risk": 0-30,
  "explanation": "Brief explanation in English"
}}

Higher semantic_risk means lower quality. If cross_question_conflict is true, semantic_risk must be at least 15."""

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        parsed = json.loads(raw)
        semantic_risk = float(parsed.get("semantic_risk", 0) or 0)
        semantic_risk = min(30.0, max(0.0, semantic_risk))
        if parsed.get("cross_question_conflict") and semantic_risk < 15:
            semantic_risk = 15.0
        reasons = []
        explanation = parsed.get("explanation")
        if explanation:
            reasons.append(str(explanation))
        if parsed.get("cross_question_conflict"):
            reasons.append("LLM detected cross-question inconsistency")
        return round(semantic_risk, 2), parsed, reasons
    except Exception as exc:
        return 0.0, {"error": str(exc)}, []


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
    run_llm: bool = True,
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
    run_llm: bool = True,
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


def evaluate_builtin_response(db: Session, survey_id: int, response_id: int, *, run_llm: bool = True) -> QualityScoreResult:
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
