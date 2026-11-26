"""Explicit semantic review using the existing Claude integration."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from app.models import Question
from typing import Tuple
import hashlib
import json
import os
import random
import re

from .normalization import (
    _is_empty,
)

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
        usage = getattr(message, "usage", None)
        parsed["_usage"] = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "model": str(getattr(message, "model", None) or "claude-haiku-4-5-20251001"),
        }
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
