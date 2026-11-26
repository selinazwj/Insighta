"""Quality result and imported-question data structures."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from dataclasses import dataclass
from dataclasses import field


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
