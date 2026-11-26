"""Existing rule-based response quality deductions."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from app.models import Question
from typing import Tuple
from statistics import median
import re

from .constants import (
    BULK_SUBMIT_THRESHOLD,
    BULK_SUBMIT_WINDOW_HOURS,
    IP_BULK_THRESHOLD,
    LOGIC_CONFLICT_RULES,
    LOW_QUALITY_TEXT_TERMS,
)
from .normalization import (
    _answer_matches_patterns,
    _answer_text,
    _is_empty,
    _normalize_text,
    _question_matches_keywords,
    _safe_float,
)

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
