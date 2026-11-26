"""Deterministic heuristic and statistical anomaly scoring."""
from __future__ import annotations

from typing import Dict
from typing import List
from typing import Tuple
from statistics import median


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
