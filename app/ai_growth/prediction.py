"""Claude-only completion prediction and recommendations.

The implementation lives in the domain modules below. This facade preserves
the original import path, including private helpers and patched collaborators.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import os
from types import SimpleNamespace
from typing import Any, Iterable, Optional
from sqlalchemy.orm import Session
from app.models import Question, Response, Survey, User
from app.ai_growth.matching import survey_match_result
from app.ai_growth.models import JumpEvent, RespondentPrediction, UserActivityEvent
from app.ai_growth.segments import user_segment_key, user_segment_label, rebuild_segment_stats
from app.ai_growth.llm import (
    claude_model_name,
    llm_batch_size,
    llm_cache_hours,
    predict_one_with_claude,
    predict_participants_with_claude,
    preview_survey_with_claude,
    rank_surveys_with_claude,
    safe_survey_payload,
    safe_user_payload,
    summarize_survey_with_claude,
)

from app.compat import install_compatibility_exports as _install_compatibility_exports
from app.ai_growth.predictions import config as _config
from app.ai_growth.predictions import common as _common
from app.ai_growth.predictions import normalization as _normalization
from app.ai_growth.predictions import context as _context
from app.ai_growth.predictions import cache as _cache
from app.ai_growth.predictions import completion as _completion
from app.ai_growth.predictions import ranking as _ranking
from app.ai_growth.predictions import summaries as _summaries

_install_compatibility_exports(
    __name__,
    {
        'datetime': (_common, 'datetime'),
        'timedelta': (_common, 'timedelta'),
        'os': (_completion, 'os'),
        'SimpleNamespace': (_summaries, 'SimpleNamespace'),
        'Any': (_common, 'Any'),
        'Iterable': (_common, 'Iterable'),
        'Optional': (_common, 'Optional'),
        'Session': (_context, 'Session'),
        'Question': (_context, 'Question'),
        'Response': (_context, 'Response'),
        'Survey': (_normalization, 'Survey'),
        'User': (_normalization, 'User'),
        'survey_match_result': (_context, 'survey_match_result'),
        'JumpEvent': (_context, 'JumpEvent'),
        'RespondentPrediction': (_cache, 'RespondentPrediction'),
        'UserActivityEvent': (_context, 'UserActivityEvent'),
        'user_segment_key': (_summaries, 'user_segment_key'),
        'user_segment_label': (_normalization, 'user_segment_label'),
        'rebuild_segment_stats': (_summaries, 'rebuild_segment_stats'),
        'claude_model_name': (_config, 'claude_model_name'),
        'llm_batch_size': (_completion, 'llm_batch_size'),
        'llm_cache_hours': (_common, 'llm_cache_hours'),
        'predict_one_with_claude': (_completion, 'predict_one_with_claude'),
        'predict_participants_with_claude': (_completion, 'predict_participants_with_claude'),
        'preview_survey_with_claude': (_summaries, 'preview_survey_with_claude'),
        'rank_surveys_with_claude': (_ranking, 'rank_surveys_with_claude'),
        'safe_survey_payload': (_context, 'safe_survey_payload'),
        'safe_user_payload': (_context, 'safe_user_payload'),
        'summarize_survey_with_claude': (_summaries, 'summarize_survey_with_claude'),
        'MODEL_VERSION': (_config, 'MODEL_VERSION'),
        '_clamp_probability': (_normalization, '_clamp_probability'),
        '_clean_confidence': (_normalization, '_clean_confidence'),
        '_clean_list': (_normalization, '_clean_list'),
        '_now': (_common, '_now'),
        '_cache_expires_at': (_common, '_cache_expires_at'),
        '_chunked': (_common, '_chunked'),
        '_safe_rate': (_common, '_safe_rate'),
        'user_history_features': (_context, 'user_history_features'),
        'survey_context_features': (_context, 'survey_context_features'),
        '_eligibility_context': (_context, '_eligibility_context'),
        '_single_prediction_payload': (_context, '_single_prediction_payload'),
        '_participant_batch_payload': (_context, '_participant_batch_payload'),
        '_survey_batch_payload': (_context, '_survey_batch_payload'),
        '_unavailable_prediction': (_normalization, '_unavailable_prediction'),
        '_normalize_llm_prediction': (_normalization, '_normalize_llm_prediction'),
        '_prediction_from_cache': (_cache, '_prediction_from_cache'),
        '_get_cached_prediction': (_cache, '_get_cached_prediction'),
        '_cached_predictions_for_user': (_cache, '_cached_predictions_for_user'),
        'cached_recommendations_for_user': (_cache, 'cached_recommendations_for_user'),
        '_save_prediction_cache': (_cache, '_save_prediction_cache'),
        'predict_user_for_survey': (_completion, 'predict_user_for_survey'),
        'candidate_users_for_survey': (_completion, 'candidate_users_for_survey'),
        '_extract_items_by_id': (_normalization, '_extract_items_by_id'),
        'predict_users_for_survey': (_completion, 'predict_users_for_survey'),
        'top_respondents': (_completion, 'top_respondents'),
        'recommend_surveys_for_user': (_ranking, 'recommend_surveys_for_user'),
        '_summary_unavailable': (_summaries, '_summary_unavailable'),
        'survey_prediction_summary': (_summaries, 'survey_prediction_summary'),
        'preview_summary_from_payload': (_summaries, 'preview_summary_from_payload'),
        'recommended_action': (_completion, 'recommended_action'),
    },
    consumers=(
        _config,
        _common,
        _normalization,
        _context,
        _cache,
        _completion,
        _ranking,
        _summaries,
    ),
)

# Keep wildcard imports identical to the legacy module.
__all__ = [
    'Any',
    'Iterable',
    'JumpEvent',
    'MODEL_VERSION',
    'Optional',
    'Question',
    'RespondentPrediction',
    'Response',
    'Session',
    'SimpleNamespace',
    'Survey',
    'User',
    'UserActivityEvent',
    'annotations',
    'cached_recommendations_for_user',
    'candidate_users_for_survey',
    'claude_model_name',
    'datetime',
    'llm_batch_size',
    'llm_cache_hours',
    'os',
    'predict_one_with_claude',
    'predict_participants_with_claude',
    'predict_user_for_survey',
    'predict_users_for_survey',
    'preview_summary_from_payload',
    'preview_survey_with_claude',
    'rank_surveys_with_claude',
    'rebuild_segment_stats',
    'recommend_surveys_for_user',
    'recommended_action',
    'safe_survey_payload',
    'safe_user_payload',
    'summarize_survey_with_claude',
    'survey_context_features',
    'survey_match_result',
    'survey_prediction_summary',
    'timedelta',
    'top_respondents',
    'user_history_features',
    'user_segment_key',
    'user_segment_label',
]
