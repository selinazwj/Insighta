"""Stable model import surface; domain entities share one SQLAlchemy registry."""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, JSON, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

from app.model_entities.base import Base

# Import in the original declaration order to retain metadata ordering.
from app.model_entities.accounts import User
from app.model_entities.calendar import GoogleCalendarConnection, GoogleCalendarEvent
from app.model_entities.studies import Survey, StudySettlement
from app.model_entities.notifications import Notification
from app.model_entities.responses import Response
from app.model_entities.activity import UserEvent, BackgroundJob
from app.model_entities.feedback import Feedback
from app.model_entities.support import SupportThread, SupportMessage
from app.model_entities.authentication import EmailVerificationCode
from app.model_entities.questionnaires import Question, Answer
from app.model_entities.quality import ResponseQualityCheck, QualityBlacklist
from app.model_entities.verification import Verification
from app.model_entities.screening import ScreeningQuestion, ScreeningAttempt
from app.model_entities.trust import RiskSignalEvent, ParticipationDecision
from app.model_entities.didit import DiditSession, DiditWebhookEvent

# Register researcher wallet tables on the shared metadata.
from app import wallet_models as _wallet_models  # noqa: E402,F401
