"""Bootstrap / schema."""

from app.schema_bootstrap import auto_schema_bootstrap_enabled
from app.models import Base
from sqlalchemy import inspect
from sqlalchemy import text
from app.database import engine


def ensure_verification_evidence_index():
    """Enforce that one external proof cannot verify several participant accounts."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_verification_method_evidence "
            "ON verifications (method, evidence_ref)"
        ))


def ensure_user_profile_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("users")}
    profile_columns = {
        "first_name": "VARCHAR",
        "last_name": "VARCHAR",
        "welcome_email_sent_at": "TIMESTAMP",
        "welcome_email_role": "VARCHAR",
        "phone_number": "VARCHAR",
        "birth_year": "VARCHAR",
        "birth_month": "VARCHAR",
        "profile_description": "VARCHAR",
        "current_country": "VARCHAR",
        "current_province": "VARCHAR",
        "current_city": "VARCHAR",
        "origin_country": "VARCHAR",
        "origin_province": "VARCHAR",
        "origin_city": "VARCHAR",
        "race": "VARCHAR",
        "income_level": "VARCHAR",
        "lifestyle_tags": "VARCHAR",
        "occupation": "VARCHAR",
        "verification_status": "VARCHAR",
        "verified_at": "TIMESTAMP",
        "verified_tier": "VARCHAR",
        "verified_email": "VARCHAR",
        "referral_code": "VARCHAR",
        "invited_by_user_id": "INTEGER",
        "student_status": "VARCHAR",
        "year_in_school": "VARCHAR",
        "international_domestic": "VARCHAR",
        "experience_tags": "VARCHAR",
        "participation_format": "VARCHAR",
        "device_type": "VARCHAR",
        "oauth_provider": "VARCHAR",
        "oauth_id": "VARCHAR",
        "stripe_account_id": "VARCHAR",
        "stripe_onboarding_complete": "VARCHAR",
        "pending_earnings": "FLOAT",
        "total_withdrawn": "FLOAT",
    }
    with engine.begin() as conn:
        for name, column_type in profile_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {column_type}"))
        # Unique index for referral codes (ignore if already present)
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code)"
            ))
        except Exception:
            pass


def ensure_survey_listing_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("surveys")}
    listing_columns = {
        "target_student_status": "VARCHAR",
        "target_year_in_school": "VARCHAR",
        "target_international_domestic": "VARCHAR",
        "target_experience_tags": "VARCHAR",
        "target_participation_format": "VARCHAR",
        "target_device": "VARCHAR",
        "urgency_level": "VARCHAR",
        "incentive_type": "VARCHAR",
        "task_type": "VARCHAR",
        "target_income_level": "VARCHAR",
        "target_lifestyle_tags": "VARCHAR",
        "target_niche_requirements": "VARCHAR",
        "participant_benefits": "VARCHAR",
        "required_occupation": "VARCHAR",
        "required_verification_tier": "VARCHAR DEFAULT 'tier_3'",
        "required_email_domains": "VARCHAR",
        "raffle_prize_type": "VARCHAR",
        "availability_slots": "TEXT",
        "interview_location": "VARCHAR",
        "session_count": "INTEGER",
        "sessions_per_week": "INTEGER",
        "admin_display_reward_amount": "FLOAT",
        "share_slug": "VARCHAR",
        "publish_token": "VARCHAR",
        "total_budget": "FLOAT",
        "per_person_gross": "FLOAT",
        "commission_rate": "FLOAT",
        "pricing_snapshot": "JSON",
        "funding_source": "VARCHAR",
        "refund_destination": "VARCHAR",
        "deadline_at": "TIMESTAMP",
        "close_reason": "VARCHAR",
        "stripe_funding_payment_intent_id": "VARCHAR",
        "payment_status": "VARCHAR",
        "stripe_payment_intent_id": "VARCHAR",
        "quality_auto_filter_enabled": "BOOLEAN DEFAULT false",
        "quality_auto_filter_min_score": "FLOAT DEFAULT 80.0",
    }
    with engine.begin() as conn:
        for name, column_type in listing_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE surveys ADD COLUMN {name} {column_type}"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_surveys_publish_token ON surveys (publish_token)"))


def ensure_settlement_wallet_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("study_settlements")}
    with engine.begin() as conn:
        for name, column_type in {"refund_destination": "VARCHAR", "refund_gross_cents": "INTEGER"}.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE study_settlements ADD COLUMN {name} {column_type}"))


def ensure_response_tracking_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("responses")}
    response_columns = {
        "client_ip": "VARCHAR",
        "user_agent": "VARCHAR",
        "device_fingerprint": "VARCHAR",
        "booking_slot": "TEXT",
        "start_followup_scheduled_at": "TIMESTAMP",
        "start_followup_sent_at": "TIMESTAMP",
        "payout_status": "VARCHAR",
        "payout_amount": "FLOAT",
        "stripe_transfer_id": "VARCHAR",
        "review_due_at": "TIMESTAMP",
        "payout_reviewed_at": "TIMESTAMP",
    }
    with engine.begin() as conn:
        for name, column_type in response_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE responses ADD COLUMN {name} {column_type}"))


def ensure_user_event_table():
    id_type = "SERIAL PRIMARY KEY" if engine.dialect.name == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    metadata_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS user_events (
                id {id_type},
                user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                anonymous_id VARCHAR NULL,
                event_name VARCHAR NOT NULL,
                target_type VARCHAR NULL,
                target_id VARCHAR NULL,
                page_path TEXT NULL,
                metadata_json {metadata_type} NULL,
                user_agent TEXT NULL,
                client_ip VARCHAR NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))


def ensure_phase_two_trust_schema():
    """Backfill columns added after the first screening release."""
    screening_columns = {
        col["name"] for col in inspect(engine).get_columns("screening_questions")
    }
    additions = {
        "updated_at": "TIMESTAMP",
        "approved_at": "TIMESTAMP",
        "approved_by": "INTEGER",
    }
    risk_columns = {
        col["name"] for col in inspect(engine).get_columns("risk_signal_events")
    }
    with engine.begin() as conn:
        for name, column_type in additions.items():
            if name not in screening_columns:
                conn.execute(text(
                    f"ALTER TABLE screening_questions ADD COLUMN {name} {column_type}"
                ))
        if "survey_id" not in risk_columns:
            conn.execute(text(
                "ALTER TABLE risk_signal_events ADD COLUMN survey_id INTEGER"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_risk_signal_events_survey_id "
            "ON risk_signal_events (survey_id)"
        ))


def initialize_database():
    """Run the original guarded compatibility bootstrap, in its original order."""
    if not auto_schema_bootstrap_enabled():
        return
    Base.metadata.create_all(bind=engine)
    ensure_verification_evidence_index()
    ensure_user_profile_columns()
    ensure_survey_listing_columns()
    ensure_settlement_wallet_columns()
    ensure_response_tracking_columns()
    ensure_user_event_table()
    ensure_phase_two_trust_schema()


__all__ = ['ensure_verification_evidence_index', 'ensure_user_profile_columns', 'ensure_survey_listing_columns', 'ensure_settlement_wallet_columns', 'ensure_response_tracking_columns', 'ensure_user_event_table', 'ensure_phase_two_trust_schema']
