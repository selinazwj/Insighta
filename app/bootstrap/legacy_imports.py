"""Original api.main import aliases retained for external scripts and patch targets."""

from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, Cookie, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, PlainTextResponse, Response as HTTPResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from passlib.context import CryptContext
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, JSON, Boolean, case, inspect, text, or_, and_
from pathlib import Path
from typing import Any, List, Optional
from datetime import datetime, timedelta, timezone, date, time
from urllib.parse import urlencode, urlsplit
from xml.sax.saxutils import escape as xml_escape
from io import BytesIO
import logging
import random
import re
import shutil
import uuid
import os
import secrets
import hashlib
import hmac
import html
import json
import stripe
import smtplib
import httpx
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from app.settlements import (assert_study_open, ensure_settlement, run_settlement_cycle, settlement_payload, lock_study, process_refund)
from app import wallet as researcher_wallet
from app.wallet_models import WalletRefundPart
from app.stripe_data import plain_stripe_data
from app.pricing import calculate_quote, pricing_config, PricingError
from app.booking_calendar import booked_events, calendar_document, google_calendar_links
from app.google_calendar import register_calendar_routes, calendar_status, queue_calendar_sync, sync_booking, JOB_TYPE as GOOGLE_CALENDAR_SYNC
from app.database import engine, get_db, SessionLocal
from app import niche
from app import withdrawals
from app.niche_models import NicheRequest, NicheQuote
from app.models import StudySettlement, Base, User, Survey, Response, Feedback, Notification, EmailVerificationCode, Question, Answer, ResponseQualityCheck, QualityBlacklist, SupportThread, SupportMessage, UserEvent, Verification, ScreeningQuestion, ScreeningAttempt, RiskSignalEvent, ParticipationDecision, BackgroundJob, GoogleCalendarEvent, DiditSession, DiditWebhookEvent
from app.schema_bootstrap import auto_schema_bootstrap_enabled
from app.observability import configure_logging, request_duration_ms, request_id_from, request_timer_start, slow_request_threshold_ms
from app.jobs.service import (
    ADMIN_BULK_EMAIL,
    AI_PREDICTION,
    AI_QUALITY_REVIEW,
    BOOKING_CONFIRMATION_EMAIL,
    DASHBOARD_RECOMMENDATIONS_REFRESH,
    DEFAULT_STALE_RUNNING_MINUTES,
    DONE,
    FAILED,
    PENDING,
    QUALITY_EXCEL_IMPORT,
    REWARD_SETUP_REMINDER_EMAIL,
    RUNNING,
    SURVEY_START_FOLLOWUP_EMAIL,
    TRUST_DATA_CLEANUP,
    DIDIT_WEBHOOK_PROCESS,
    DIDIT_SESSION_RECONCILE,
    enqueue_admin_bulk_email_job,
    enqueue_didit_session_reconcile_job,
    enqueue_didit_webhook_process_job,
    enqueue_booking_confirmation_job,
    enqueue_dashboard_recommendations_refresh_job,
    enqueue_ai_quality_review_job,
    enqueue_quality_excel_import_job,
    enqueue_survey_start_followup_job,
    enqueue_trust_data_cleanup_job,
    run_due_jobs,
)
from app.ai_usage import (
    AiUsageLimitError,
    ensure_usage_available,
    find_recent_ai_job,
    job_public_payload,
    reserve_usage,
    update_usage,
)
from app.quality_engine import (
    anthropic_api_key_configured,
    batch_anomaly_scores_for_features,
    compute_excel_row_quality,
    create_excel_quality_check,
    _duration_seconds_between,
    apply_auto_approve_checks,
    ensure_builtin_quality_checks,
    evaluate_builtin_response,
    resolve_excel_row_context,
    upsert_builtin_quality_check,
)
from app.screening_engine import (
    evaluate_screening,
    generate_screening_draft,
    validate_researcher_edit,
)
from app.payouts import (
    APPROVED,
    LEGACY_RELEASED,
    mark_response_under_review,
    reject_response_payout,
    release_response_payout,
    return_response_to_review,
)
from app.bot_defense import (
    check_rate_limit,
    client_ip,
    make_captcha,
    turnstile_site_key,
    verify_bot_challenge,
    honeypot_triggered,
)
from app.verification.assurance import (
    HumanAssessment,
    HumanSignals,
    TIER_BASIC,
    TIER_IDENTITY,
    TIER_LABELS,
    TIER_RANK,
    TIER_TRUSTED,
    assess_human,
    evidence_reference,
    strongest_tier_for_methods,
    tier_rank,
    tier_satisfies,
)
from app.verification.didit_client import create_session as create_didit_session, didit_configured
from app.verification.didit import (
    is_didit_test_webhook,
    parse_vendor_user_id,
    verify_didit_webhook,
    webhook_secrets as didit_webhook_secrets,
)
from app.verification.didit_processor import (
    DiditWebhookDuplicate,
    DiditWebhookError,
    DiditWebhookIgnored,
    apply_didit_status_update,
    enqueue_stale_didit_reconcile_jobs,
    process_didit_webhook_body,
    reconcile_didit_session,
)
from app.trust_engine import (
    BLOCK as TRUST_BLOCK,
    ParticipationInputs,
    public_reason_codes,
    recommend_participation,
)
from app.ai_growth.routes import router as ai_growth_router
from app.ai_growth.security import is_safe_internal_next
from app.ai_growth.jump import mark_latest_jump_completed_for_response
from app.ai_growth.llm import collect_llm_usage
from app.ai_growth.prediction import (
    cached_recommendations_for_user,
    recommend_surveys_for_user,
    survey_prediction_summary,
    top_respondents,
)
from app.ai_growth.models import JumpEvent, RespondentPrediction, SurveySegmentStats, UserActivityEvent
from app.discovery.router import router as discovery_router
from app.discovery.discovery import discover as discover_channels
from app.discovery.models import Criteria as DiscoveryCriteria
from app.discovery.ranking import rank as rank_discovery_channels
from app.seo import (
    CATEGORY_CONTENT,
    INDEX_PUBLIC_STUDIES,
    SITE_LANGUAGE,
    category_content,
    category_image,
    category_label,
    content_page_seo,
    home_seo,
    participant_seo,
    plain_text,
    site_url as seo_site_url,
    studies_directory_seo,
    category_seo,
    study_seo,
)
from app.auth_session import SignedSessionMiddleware, sign_session
from app.wallet_routes import register_wallet_routes
from app.niche_routes import register_niche_routes

__all__ = ['FastAPI', 'APIRouter', 'Request', 'Form', 'Depends', 'HTTPException', 'Cookie', 'UploadFile', 'File', 'Query', 'HTMLResponse', 'RedirectResponse', 'JSONResponse', 'StreamingResponse', 'PlainTextResponse', 'HTTPResponse', 'Jinja2Templates', 'StaticFiles', 'GZipMiddleware', 'Session', 'IntegrityError', 'CryptContext', 'Column', 'Integer', 'String', 'Float', 'DateTime', 'ForeignKey', 'func', 'JSON', 'Boolean', 'case', 'inspect', 'text', 'or_', 'and_', 'Path', 'Any', 'List', 'Optional', 'datetime', 'timedelta', 'timezone', 'date', 'time', 'urlencode', 'urlsplit', 'xml_escape', 'BytesIO', 'logging', 'random', 're', 'shutil', 'uuid', 'os', 'secrets', 'hashlib', 'hmac', 'html', 'json', 'stripe', 'smtplib', 'httpx', 'asyncio', 'MIMEText', 'MIMEMultipart', 'load_dotenv', 'assert_study_open', 'ensure_settlement', 'run_settlement_cycle', 'settlement_payload', 'lock_study', 'process_refund', 'researcher_wallet', 'WalletRefundPart', 'plain_stripe_data', 'calculate_quote', 'pricing_config', 'PricingError', 'booked_events', 'calendar_document', 'google_calendar_links', 'register_calendar_routes', 'calendar_status', 'queue_calendar_sync', 'sync_booking', 'GOOGLE_CALENDAR_SYNC', 'engine', 'get_db', 'SessionLocal', 'niche', 'withdrawals', 'NicheRequest', 'NicheQuote', 'StudySettlement', 'Base', 'User', 'Survey', 'Response', 'Feedback', 'Notification', 'EmailVerificationCode', 'Question', 'Answer', 'ResponseQualityCheck', 'QualityBlacklist', 'SupportThread', 'SupportMessage', 'UserEvent', 'Verification', 'ScreeningQuestion', 'ScreeningAttempt', 'RiskSignalEvent', 'ParticipationDecision', 'BackgroundJob', 'GoogleCalendarEvent', 'DiditSession', 'DiditWebhookEvent', 'auto_schema_bootstrap_enabled', 'configure_logging', 'request_duration_ms', 'request_id_from', 'request_timer_start', 'slow_request_threshold_ms', 'ADMIN_BULK_EMAIL', 'AI_PREDICTION', 'AI_QUALITY_REVIEW', 'BOOKING_CONFIRMATION_EMAIL', 'DASHBOARD_RECOMMENDATIONS_REFRESH', 'DEFAULT_STALE_RUNNING_MINUTES', 'DONE', 'FAILED', 'PENDING', 'QUALITY_EXCEL_IMPORT', 'REWARD_SETUP_REMINDER_EMAIL', 'RUNNING', 'SURVEY_START_FOLLOWUP_EMAIL', 'TRUST_DATA_CLEANUP', 'DIDIT_WEBHOOK_PROCESS', 'DIDIT_SESSION_RECONCILE', 'enqueue_admin_bulk_email_job', 'enqueue_didit_session_reconcile_job', 'enqueue_didit_webhook_process_job', 'enqueue_booking_confirmation_job', 'enqueue_dashboard_recommendations_refresh_job', 'enqueue_ai_quality_review_job', 'enqueue_quality_excel_import_job', 'enqueue_survey_start_followup_job', 'enqueue_trust_data_cleanup_job', 'run_due_jobs', 'AiUsageLimitError', 'ensure_usage_available', 'find_recent_ai_job', 'job_public_payload', 'reserve_usage', 'update_usage', 'anthropic_api_key_configured', 'batch_anomaly_scores_for_features', 'compute_excel_row_quality', 'create_excel_quality_check', '_duration_seconds_between', 'apply_auto_approve_checks', 'ensure_builtin_quality_checks', 'evaluate_builtin_response', 'resolve_excel_row_context', 'upsert_builtin_quality_check', 'evaluate_screening', 'generate_screening_draft', 'validate_researcher_edit', 'APPROVED', 'LEGACY_RELEASED', 'mark_response_under_review', 'reject_response_payout', 'release_response_payout', 'return_response_to_review', 'check_rate_limit', 'client_ip', 'make_captcha', 'turnstile_site_key', 'verify_bot_challenge', 'honeypot_triggered', 'HumanAssessment', 'HumanSignals', 'TIER_BASIC', 'TIER_IDENTITY', 'TIER_LABELS', 'TIER_RANK', 'TIER_TRUSTED', 'assess_human', 'evidence_reference', 'strongest_tier_for_methods', 'tier_rank', 'tier_satisfies', 'create_didit_session', 'didit_configured', 'is_didit_test_webhook', 'parse_vendor_user_id', 'verify_didit_webhook', 'didit_webhook_secrets', 'DiditWebhookDuplicate', 'DiditWebhookError', 'DiditWebhookIgnored', 'apply_didit_status_update', 'enqueue_stale_didit_reconcile_jobs', 'process_didit_webhook_body', 'reconcile_didit_session', 'TRUST_BLOCK', 'ParticipationInputs', 'public_reason_codes', 'recommend_participation', 'ai_growth_router', 'is_safe_internal_next', 'mark_latest_jump_completed_for_response', 'collect_llm_usage', 'cached_recommendations_for_user', 'recommend_surveys_for_user', 'survey_prediction_summary', 'top_respondents', 'JumpEvent', 'RespondentPrediction', 'SurveySegmentStats', 'UserActivityEvent', 'discovery_router', 'discover_channels', 'DiscoveryCriteria', 'rank_discovery_channels', 'CATEGORY_CONTENT', 'INDEX_PUBLIC_STUDIES', 'SITE_LANGUAGE', 'category_content', 'category_image', 'category_label', 'content_page_seo', 'home_seo', 'participant_seo', 'plain_text', 'seo_site_url', 'studies_directory_seo', 'category_seo', 'study_seo', 'SignedSessionMiddleware', 'sign_session', 'register_wallet_routes', 'register_niche_routes']
