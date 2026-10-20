from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, JSON, Boolean, Text, UniqueConstraint
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()


# ======================
# User (filler & publisher)
# ======================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)

    # Core profile fields
    age_range = Column(String, nullable=True)
    birth_year = Column(String, nullable=True)
    birth_month = Column(String, nullable=True)
    profile_description = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    field = Column(String, nullable=True)
    status = Column(String, nullable=True)
    state = Column(String, nullable=True)
    current_country = Column(String, nullable=True)
    current_province = Column(String, nullable=True)
    current_city = Column(String, nullable=True)
    origin_country = Column(String, nullable=True)
    origin_province = Column(String, nullable=True)
    origin_city = Column(String, nullable=True)
    race = Column(String, nullable=True)
    ethnicity = Column(String, nullable=True)
    mental_health_diagnosis = Column(String, nullable=True)
    physical_health_diagnosis = Column(String, nullable=True)
    sexual_orientation = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    sport_frequency = Column(String, nullable=True)
    smoking = Column(String, nullable=True)
    cannabis_use = Column(String, nullable=True)
    language = Column(String, nullable=True)
    
    # ive added these for the verification feature (Phase 1)
    occupation = Column(String, nullable=True)
    verification_status = Column(String, default="unverified")
    verified_at = Column(DateTime, nullable=True)
    verified_tier = Column(String, nullable=True)
    # ive added this for the email-domain feature — the email the participant proved via OTP
    # (may differ from their account email, e.g. they verified their @bu.edu address)
    verified_email = Column(String, nullable=True)

    # Student segmentation
    student_status = Column(String, nullable=True)
    year_in_school = Column(String, nullable=True)
    international_domestic = Column(String, nullable=True)
    experience_tags = Column(String, nullable=True)
    income_level = Column(String, nullable=True)
    lifestyle_tags = Column(String, nullable=True)
    participation_format = Column(String, nullable=True)
    device_type = Column(String, nullable=True)

    # OAuth fields
    oauth_provider = Column(String, nullable=True)   # "google" | "linkedin" | None
    oauth_id = Column(String, nullable=True)          # provider's unique user id

    # Stripe fields
    stripe_account_id = Column(String, nullable=True)
    stripe_onboarding_complete = Column(String, default="false")
    pending_earnings = Column(Float, default=0.0)
    total_withdrawn = Column(Float, default=0.0)
    welcome_email_sent_at = Column(DateTime, nullable=True)
    welcome_email_role = Column(String, nullable=True)

    # Referral
    referral_code = Column(String, unique=True, index=True, nullable=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    surveys = relationship("Survey", back_populates="publisher")
    responses = relationship("Response", back_populates="participant")


class GoogleCalendarConnection(Base):
    __tablename__ = "google_calendar_connections"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    google_subject = Column(String, nullable=False)
    google_email = Column(String, nullable=False)
    refresh_token_encrypted = Column(Text, nullable=False)
    access_token_encrypted = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    needs_reconnect = Column(Boolean, nullable=False, default=False)


class GoogleCalendarEvent(Base):
    __tablename__ = "google_calendar_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    session_index = Column(Integer, nullable=False)
    event_id = Column(String, nullable=False)
    google_subject = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "response_id", "session_index"),)


# ======================
# Survey
# ======================
class Survey(Base):
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, index=True)
    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Core targeting fields
    target_age_range = Column(String, nullable=True)
    target_education_min = Column(Integer, nullable=True)
    target_education_max = Column(Integer, nullable=True)
    target_field = Column(String, nullable=True)
    target_status = Column(String, nullable=True)
    target_state = Column(String, nullable=True)
    target_language = Column(String, nullable=True)
    target_ethnicity = Column(String, nullable=True)
    target_sexual_orientation = Column(String, nullable=True)
    target_mental_health_diagnosis = Column(String, nullable=True)
    target_physical_health_diagnosis = Column(String, nullable=True)
    target_sport_type = Column(String, nullable=True)
    target_sport_frequency = Column(String, nullable=True)
    target_smoking = Column(String, nullable=True)
    target_cannabis_use = Column(String, nullable=True)
    
    # ive added these so researchers pick who can fill the survey
    required_occupation = Column(String, nullable=True)
    required_verification_tier = Column(String, default="tier_3")
    # ive added this for the email-domain feature — comma separated allowed domains
    # (no @, lowercase) e.g. "bu.edu,gmail.com". blank means any email is fine
    required_email_domains = Column(String, nullable=True)
    
    # Extended targeting fields
    target_student_status = Column(String, nullable=True)
    target_year_in_school = Column(String, nullable=True)
    target_international_domestic = Column(String, nullable=True)
    target_experience_tags = Column(String, nullable=True)
    target_participation_format = Column(String, nullable=True)
    target_device = Column(String, nullable=True)
    target_income_level = Column(String, nullable=True)
    target_lifestyle_tags = Column(String, nullable=True)
    target_niche_requirements = Column(String, nullable=True)
    participant_benefits = Column(String, nullable=True)
    availability_slots = Column(String, nullable=True)
    interview_location = Column(String, nullable=True)
    session_count = Column(Integer, nullable=True)
    sessions_per_week = Column(Integer, nullable=True)
    urgency_level = Column(String, nullable=True)
    incentive_type = Column(String, nullable=True)
    raffle_prize_type = Column(String, nullable=True)

    # Survey metadata
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)
    task_type = Column(String, default="survey", nullable=True)
    category = Column(String, nullable=False)
    estimated_time = Column(Integer, nullable=False)
    image_url = Column(String, nullable=True)
    share_slug = Column(String, unique=True, index=True, nullable=True)

    # Reward & progress
    reward_amount = Column(Float, nullable=False)
    admin_display_reward_amount = Column(Float, nullable=True)
    total_budget = Column(Float, nullable=True)
    per_person_gross = Column(Float, nullable=True)
    commission_rate = Column(Float, nullable=True)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # Status & timestamps
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Stripe payment status
    payment_status = Column(String, default="unpaid")
    stripe_payment_intent_id = Column(String, nullable=True)

    # Quality auto-filter (publisher default for Results / Excel quality checks)
    quality_auto_filter_enabled = Column(Boolean, default=False)
    quality_auto_filter_min_score = Column(Float, default=80.0)

    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")
    questions = relationship("Question", back_populates="survey")


# ======================
# Notification
# ======================
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    participant_email = Column(String, nullable=True)
    survey_title = Column(String, nullable=True)
    task_type = Column(String, default="survey")
    status = Column(String, default="pending")  # pending / accepted / rejected
    created_at = Column(DateTime, default=datetime.utcnow)


# ======================
# Response
# ======================
class Response(Base):
    __tablename__ = "responses"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="started")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Stripe payout status
    payout_status = Column(String, default="pending")
    payout_amount = Column(Float, nullable=True)
    stripe_transfer_id = Column(String, nullable=True)

    client_ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    device_fingerprint = Column(String, nullable=True)
    booking_slot = Column(String, nullable=True)
    start_followup_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    start_followup_sent_at = Column(DateTime(timezone=True), nullable=True)

    survey = relationship("Survey", back_populates="responses")
    participant = relationship("User", back_populates="responses")
    answers = relationship("Answer", back_populates="response")


# ======================
# Product analytics events
# ======================
class UserEvent(Base):
    __tablename__ = "user_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    anonymous_id = Column(String, nullable=True, index=True)
    event_name = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=True, index=True)
    target_id = Column(String, nullable=True, index=True)
    page_path = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    user_agent = Column(Text, nullable=True)
    client_ip = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", backref="events")


# ======================
# Background jobs
# ======================
class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", nullable=False, index=True)
    payload_json = Column(JSON, nullable=True)
    dedupe_key = Column(String, unique=True, index=True, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    run_after = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    locked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ======================
# Feedback
# ======================
class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=False)

    status = Column(String, default="pending")
    credit_amount = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="feedbacks")


# ======================
# Support chat
# ======================
class SupportThread(Base):
    __tablename__ = "support_threads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String, default="open")  # open / closed
    last_message_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="support_threads")
    messages = relationship("SupportMessage", back_populates="thread")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("support_threads.id"), nullable=False, index=True)
    sender_type = Column(String, nullable=False)  # user / admin / system
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)

    thread = relationship("SupportThread", back_populates="messages")


# ======================
# Email verification / password reset code
# ======================
class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    purpose = Column(String, index=True, nullable=False)
    code = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

# ======================
# Question
# ======================
class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    question_text = Column(String, nullable=False)
    question_type = Column(String, nullable=False)
    # single / multiple / text / scale / dropdown
    options = Column(JSON, nullable=True)
    is_required = Column(Boolean, default=True)
    order_index = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    survey = relationship("Survey", back_populates="questions")
    answers = relationship("Answer", back_populates="question")


# ======================
# Answer
# ======================
class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    answer_value = Column(JSON, nullable=False)
    # single_choice: "A"
    # multiple_choice: ["A", "B"]
    # text: "I think..."
    # scale: 4
    created_at = Column(DateTime, default=datetime.utcnow)

    response = relationship("Response", back_populates="answers")
    question = relationship("Question", back_populates="answers")


# ======================
# Response quality check
# ======================
class ResponseQualityCheck(Base):
    __tablename__ = "response_quality_checks"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    source_type = Column(String, default="builtin")  # builtin | excel
    source_ref = Column(String, nullable=True)       # e.g. filename:row_index

    quality_score = Column(Float, nullable=False, default=100.0)
    quality_label = Column(String, nullable=False, default="high")
    fraud_risk = Column(Boolean, default=False)

    rule_penalty = Column(Float, default=0.0)
    anomaly_score = Column(Float, default=0.0)
    semantic_risk = Column(Float, default=0.0)

    triggered_rules = Column(JSON, nullable=True)
    reasons = Column(JSON, nullable=True)
    llm_result_json = Column(JSON, nullable=True)

    review_status = Column(String, default="pending")  # pending / approved / rejected / needs_review
    reviewer_label = Column(String, nullable=True)

    raw_response_json = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QualityBlacklist(Base):
    __tablename__ = "quality_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    block_type = Column(String, nullable=False)   # ip | user | device
    block_value = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ======================
# Verification
# ======================
class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint("method", "evidence_ref", name="uq_verification_method_evidence"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    attribute = Column(String, nullable=False)   # 'is_student', 'is_physician'
    method = Column(String, nullable=False)       # 'edu_email', 'npi_registry'
    status = Column(String, default="pending")   # pending | verified | rejected
    trust_score = Column(Float, nullable=True)
    evidence_ref = Column(String, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    verified_by = Column(String, nullable=True)  # reviewer id or 'system'
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", backref="verifications")


# ======================
# Study eligibility screening
# ======================
class ScreeningQuestion(Base):
    __tablename__ = "screening_questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    attribute = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    question_type = Column(String, nullable=False, default="single")
    options = Column(JSON, nullable=False)
    accepted_answers = Column(JSON, nullable=False)
    rationale = Column(Text, nullable=True)
    sensitivity = Column(String, nullable=False, default="low")
    source = Column(String, nullable=False, default="rules")  # rules | ai_assisted | researcher_edited
    status = Column(String, nullable=False, default="draft")  # draft | approved | retired
    order_index = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    survey = relationship("Survey", backref="screening_questions")


class ScreeningAttempt(Base):
    __tablename__ = "screening_attempts"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "participant_id",
            "version",
            name="uq_screening_attempt_participant_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    answers = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="started")  # started | passed | failed | review
    risk_score = Column(Float, nullable=False, default=0.0)
    reasons = Column(JSON, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    survey = relationship("Survey", backref="screening_attempts")
    participant = relationship("User", backref="screening_attempts")


# ======================
# Persistent trust signals and participation decisions
# ======================
class RiskSignalEvent(Base):
    """Privacy-minimized security telemetry used by the human-risk engine."""

    __tablename__ = "risk_signal_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)  # registration | start | completion
    network_ref = Column(String, nullable=True, index=True)  # HMAC, never the raw IP
    device_ref = Column(String, nullable=True, index=True)   # HMAC, never the raw fingerprint
    bot_challenge_passed = Column(Boolean, nullable=True)
    signal_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", backref="risk_signal_events")
    survey = relationship("Survey", backref="risk_signal_events")


class ParticipationDecision(Base):
    """An auditable snapshot of the checks used at each participation gate."""

    __tablename__ = "participation_decisions"

    id = Column(Integer, primary_key=True, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=True, index=True)
    stage = Column(String, nullable=False, index=True)  # registration | start | completion | payout
    human_status = Column(String, nullable=False)
    human_risk_score = Column(Float, nullable=False, default=0.0)
    eligibility_status = Column(String, nullable=False, default="not_required")
    assurance_status = Column(String, nullable=False, default="passed")
    quality_status = Column(String, nullable=False, default="not_available")
    quality_score = Column(Float, nullable=True)
    outcome = Column(String, nullable=False, index=True)  # allow | review | block
    reasons = Column(JSON, nullable=True)
    decision_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    participant = relationship("User", backref="participation_decisions")
    survey = relationship("Survey", backref="participation_decisions")
    response = relationship("Response", backref="participation_decisions")
