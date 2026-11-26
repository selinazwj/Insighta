"""Response quality engine.

The implementation lives in the domain modules below. This facade preserves
the original import path, including private helpers and patched collaborators.
"""
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

from app.compat import install_compatibility_exports as _install_compatibility_exports
from app.quality import constants as _constants
from app.quality import types as _types
from app.quality import normalization as _normalization
from app.quality import features as _features
from app.quality import anomaly as _anomaly
from app.quality import rules as _rules
from app.quality import semantic as _semantic
from app.quality import scoring as _scoring
from app.quality import excel as _excel
from app.quality import repository as _repository
from app.quality import service as _service

_install_compatibility_exports(
    __name__,
    {
        'hashlib': (_semantic, 'hashlib'),
        'json': (_semantic, 'json'),
        'os': (_semantic, 'os'),
        'random': (_semantic, 'random'),
        're': (_rules, 're'),
        'dataclass': (_types, 'dataclass'),
        'field': (_types, 'field'),
        'datetime': (_normalization, 'datetime'),
        'timedelta': (_repository, 'timedelta'),
        'timezone': (_normalization, 'timezone'),
        'median': (_anomaly, 'median'),
        'Any': (_types, 'Any'),
        'Dict': (_types, 'Dict'),
        'Iterable': (_normalization, 'Iterable'),
        'List': (_types, 'List'),
        'Optional': (_types, 'Optional'),
        'Tuple': (_anomaly, 'Tuple'),
        'Session': (_features, 'Session'),
        'Answer': (_features, 'Answer'),
        'QualityBlacklist': (_repository, 'QualityBlacklist'),
        'Question': (_features, 'Question'),
        'Response': (_features, 'Response'),
        'ResponseQualityCheck': (_repository, 'ResponseQualityCheck'),
        'Survey': (_service, 'Survey'),
        'User': (_service, 'User'),
        'LOW_QUALITY_TEXT_TERMS': (_constants, 'LOW_QUALITY_TEXT_TERMS'),
        'NEGATIVE_ANSWERS': (_constants, 'NEGATIVE_ANSWERS'),
        'LOGIC_CONFLICT_RULES': (_constants, 'LOGIC_CONFLICT_RULES'),
        'BULK_SUBMIT_WINDOW_HOURS': (_constants, 'BULK_SUBMIT_WINDOW_HOURS'),
        'BULK_SUBMIT_THRESHOLD': (_constants, 'BULK_SUBMIT_THRESHOLD'),
        'IP_BULK_THRESHOLD': (_constants, 'IP_BULK_THRESHOLD'),
        'EXCEL_METADATA_HEADERS': (_constants, 'EXCEL_METADATA_HEADERS'),
        '_PseudoQuestion': (_types, '_PseudoQuestion'),
        'QualityScoreResult': (_types, 'QualityScoreResult'),
        '_is_empty': (_normalization, '_is_empty'),
        '_safe_float': (_normalization, '_safe_float'),
        '_duration_seconds_between': (_normalization, '_duration_seconds_between'),
        '_normalize_text': (_normalization, '_normalize_text'),
        '_answer_text': (_normalization, '_answer_text'),
        '_label_from_score': (_normalization, '_label_from_score'),
        '_review_status_from_label': (_normalization, '_review_status_from_label'),
        '_question_matches_keywords': (_normalization, '_question_matches_keywords'),
        '_answer_matches_patterns': (_normalization, '_answer_matches_patterns'),
        '_extract_response_features': (_features, '_extract_response_features'),
        '_heuristic_anomaly_score': (_anomaly, '_heuristic_anomaly_score'),
        '_isolation_forest_anomaly_score': (_anomaly, '_isolation_forest_anomaly_score'),
        'batch_anomaly_scores_for_features': (_anomaly, 'batch_anomaly_scores_for_features'),
        '_check_blacklist': (_repository, '_check_blacklist'),
        '_submission_stats': (_repository, '_submission_stats'),
        '_compute_rule_penalty': (_rules, '_compute_rule_penalty'),
        '_should_run_llm': (_semantic, '_should_run_llm'),
        'get_anthropic_api_key': (_semantic, 'get_anthropic_api_key'),
        'anthropic_api_key_configured': (_semantic, 'anthropic_api_key_configured'),
        '_build_semantic_qa_pairs': (_semantic, '_build_semantic_qa_pairs'),
        '_mock_semantic_eval': (_semantic, '_mock_semantic_eval'),
        '_run_llm_semantic_eval': (_semantic, '_run_llm_semantic_eval'),
        '_normalize_excel_header': (_excel, '_normalize_excel_header'),
        '_is_excel_metadata_header': (_excel, '_is_excel_metadata_header'),
        'build_pseudo_question_map_from_row': (_excel, 'build_pseudo_question_map_from_row'),
        'resolve_excel_row_context': (_excel, 'resolve_excel_row_context'),
        'compute_excel_row_quality': (_excel, 'compute_excel_row_quality'),
        'compute_quality_score': (_scoring, 'compute_quality_score'),
        'build_peer_features': (_features, 'build_peer_features'),
        'evaluate_builtin_response': (_service, 'evaluate_builtin_response'),
        'apply_auto_approve_checks': (_repository, 'apply_auto_approve_checks'),
        'ensure_builtin_quality_checks': (_service, 'ensure_builtin_quality_checks'),
        'upsert_builtin_quality_check': (_repository, 'upsert_builtin_quality_check'),
        'create_excel_quality_check': (_repository, 'create_excel_quality_check'),
    },
    consumers=(
        _constants,
        _types,
        _normalization,
        _features,
        _anomaly,
        _rules,
        _semantic,
        _scoring,
        _excel,
        _repository,
        _service,
    ),
)

# Keep wildcard imports identical to the legacy module.
__all__ = [
    'Answer',
    'Any',
    'BULK_SUBMIT_THRESHOLD',
    'BULK_SUBMIT_WINDOW_HOURS',
    'Dict',
    'EXCEL_METADATA_HEADERS',
    'IP_BULK_THRESHOLD',
    'Iterable',
    'LOGIC_CONFLICT_RULES',
    'LOW_QUALITY_TEXT_TERMS',
    'List',
    'NEGATIVE_ANSWERS',
    'Optional',
    'QualityBlacklist',
    'QualityScoreResult',
    'Question',
    'Response',
    'ResponseQualityCheck',
    'Session',
    'Survey',
    'Tuple',
    'User',
    'annotations',
    'anthropic_api_key_configured',
    'apply_auto_approve_checks',
    'batch_anomaly_scores_for_features',
    'build_peer_features',
    'build_pseudo_question_map_from_row',
    'compute_excel_row_quality',
    'compute_quality_score',
    'create_excel_quality_check',
    'dataclass',
    'datetime',
    'ensure_builtin_quality_checks',
    'evaluate_builtin_response',
    'field',
    'get_anthropic_api_key',
    'hashlib',
    'json',
    'median',
    'os',
    'random',
    're',
    'resolve_excel_row_context',
    'timedelta',
    'timezone',
    'upsert_builtin_quality_check',
]
