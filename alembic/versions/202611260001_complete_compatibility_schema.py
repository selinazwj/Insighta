"""Complete the additive schema previously maintained only at app startup.

This also repairs installations already stamped at the former head, which do
not traverse the newly supplied base revision. Existing rows are never rebuilt
or removed; only absent tables, known compatibility columns and indexes are added.
"""
from alembic import op
import sqlalchemy as sa
from app.model_registry import Base

revision = "202611260001"
down_revision = "202611210001"
branch_labels = None
depends_on = None

COMPATIBILITY_COLUMNS = {'users': ('first_name',
           'last_name',
           'welcome_email_sent_at',
           'welcome_email_role',
           'phone_number',
           'birth_year',
           'birth_month',
           'profile_description',
           'current_country',
           'current_province',
           'current_city',
           'origin_country',
           'origin_province',
           'origin_city',
           'race',
           'income_level',
           'lifestyle_tags',
           'occupation',
           'verification_status',
           'verified_at',
           'verified_tier',
           'verified_email',
           'referral_code',
           'invited_by_user_id',
           'student_status',
           'year_in_school',
           'international_domestic',
           'experience_tags',
           'participation_format',
           'device_type',
           'oauth_provider',
           'oauth_id',
           'stripe_account_id',
           'stripe_onboarding_complete',
           'pending_earnings',
           'total_withdrawn'),
 'surveys': ('target_student_status',
             'target_year_in_school',
             'target_international_domestic',
             'target_experience_tags',
             'target_participation_format',
             'target_device',
             'urgency_level',
             'incentive_type',
             'task_type',
             'target_income_level',
             'target_lifestyle_tags',
             'target_niche_requirements',
             'participant_benefits',
             'required_occupation',
             'required_verification_tier',
             'required_email_domains',
             'raffle_prize_type',
             'availability_slots',
             'interview_location',
             'session_count',
             'sessions_per_week',
             'admin_display_reward_amount',
             'share_slug',
             'publish_token',
             'total_budget',
             'per_person_gross',
             'commission_rate',
             'pricing_snapshot',
             'funding_source',
             'refund_destination',
             'deadline_at',
             'close_reason',
             'stripe_funding_payment_intent_id',
             'payment_status',
             'stripe_payment_intent_id',
             'quality_auto_filter_enabled',
             'quality_auto_filter_min_score'),
 'responses': ('client_ip',
               'user_agent',
               'device_fingerprint',
               'booking_slot',
               'start_followup_scheduled_at',
               'start_followup_sent_at',
               'payout_status',
               'payout_amount',
               'stripe_transfer_id',
               'review_due_at',
               'payout_reviewed_at'),
 'screening_questions': ('updated_at', 'approved_at', 'approved_by'),
 'study_settlements': ('refund_destination', 'refund_gross_cents'),
 'risk_signal_events': ('survey_id',)}

# Earlier revisions skipped these when the owning tables were absent.
GROWTH_INDEXES = [('ix_responses_participant_survey', 'responses', ['participant_id', 'survey_id']),
 ('ix_responses_survey_status', 'responses', ['survey_id', 'status']),
 ('ix_responses_participant_status_completed',
  'responses',
  ['participant_id', 'status', 'completed_at']),
 ('ix_surveys_status_published', 'surveys', ['status', 'published_at']),
 ('ix_surveys_publisher_created', 'surveys', ['publisher_id', 'created_at']),
 ('ix_user_events_target_created', 'user_events', ['target_type', 'target_id', 'created_at']),
 ('ix_user_events_user_created', 'user_events', ['user_id', 'created_at']),
 ('ix_user_events_event_created', 'user_events', ['event_name', 'created_at']),
 ('ix_notifications_participant_status', 'notifications', ['participant_id', 'status']),
 ('ix_notifications_publisher_status', 'notifications', ['publisher_id', 'status']),
 ('ix_answers_response_question', 'answers', ['response_id', 'question_id']),
 ('ix_questions_survey_order', 'questions', ['survey_id', 'order_index']),
 ('ix_quality_checks_survey_status', 'response_quality_checks', ['survey_id', 'review_status']),
 ('ix_support_threads_status_last_message', 'support_threads', ['status', 'last_message_at']),
 ('ix_support_messages_thread_created', 'support_messages', ['thread_id', 'created_at']),
 ('ix_jump_events_survey_status', 'jump_events', ['survey_id', 'status']),
 ('ix_jump_events_participant_clicked', 'jump_events', ['participant_id', 'clicked_at']),
 ('ix_respondent_predictions_survey_expires',
  'respondent_predictions',
  ['survey_id', 'expires_at']),
 ('ix_user_activity_events_user_created', 'user_activity_events', ['user_id', 'created_at']),
 ('ix_user_activity_events_survey_type_created',
  'user_activity_events',
  ['survey_id', 'event_type', 'created_at'])]

# Match the defaults of the existing startup compatibility helpers.
COLUMN_DEFAULTS = {
    ("surveys", "required_verification_tier"): sa.text("'tier_3'"),
    ("surveys", "quality_auto_filter_enabled"): sa.text("false"),
    ("surveys", "quality_auto_filter_min_score"): sa.text("80.0"),
}


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    for table_name, names in COMPATIBILITY_COLUMNS.items():
        present = {c["name"] for c in sa.inspect(bind).get_columns(table_name)}
        table = Base.metadata.tables[table_name]
        for name in names:
            if name not in present:
                column = table.columns[name]
                # The legacy compatibility additions are nullable, even where
                # newly created tables define stronger initial constraints.
                op.add_column(table_name, sa.Column(
                    name, column.type, nullable=True,
                    server_default=COLUMN_DEFAULTS.get((table_name, name)),
                ))

    # create_all does not create missing indexes on tables that already exist.
    for table in Base.metadata.sorted_tables:
        inspector = sa.inspect(bind)
        present = {i["name"] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in present:
                index.create(bind=bind)
                present.add(index.name)

    for name, table_name, columns in GROWTH_INDEXES:
        present = {i["name"] for i in sa.inspect(bind).get_indexes(table_name)}
        if name not in present:
            op.create_index(name, table_name, columns)

    inspector = sa.inspect(bind)
    proof_indexes = {i["name"] for i in inspector.get_indexes("verifications")}
    proof_constraints = {c["name"] for c in inspector.get_unique_constraints("verifications")}
    if "uq_verification_method_evidence" not in proof_indexes | proof_constraints:
        op.create_index("uq_verification_method_evidence", "verifications", ["method", "evidence_ref"], unique=True)


def downgrade():
    # These additions can contain production data and may predate Alembic.
    # Keep them when moving the version marker back to the previous release.
    pass
