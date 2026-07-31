"""add growth path indexes

Revision ID: 202607220001
Revises:
Create Date: 2026-07-22 00:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202607220001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = [
    ("ix_responses_participant_survey", "responses", ["participant_id", "survey_id"]),
    ("ix_responses_survey_status", "responses", ["survey_id", "status"]),
    ("ix_responses_participant_status_completed", "responses", ["participant_id", "status", "completed_at"]),
    ("ix_surveys_status_published", "surveys", ["status", "published_at"]),
    ("ix_surveys_publisher_created", "surveys", ["publisher_id", "created_at"]),
    ("ix_user_events_target_created", "user_events", ["target_type", "target_id", "created_at"]),
    ("ix_user_events_user_created", "user_events", ["user_id", "created_at"]),
    ("ix_user_events_event_created", "user_events", ["event_name", "created_at"]),
    ("ix_notifications_participant_status", "notifications", ["participant_id", "status"]),
    ("ix_notifications_publisher_status", "notifications", ["publisher_id", "status"]),
    ("ix_answers_response_question", "answers", ["response_id", "question_id"]),
    ("ix_questions_survey_order", "questions", ["survey_id", "order_index"]),
    ("ix_quality_checks_survey_status", "response_quality_checks", ["survey_id", "review_status"]),
    ("ix_support_threads_status_last_message", "support_threads", ["status", "last_message_at"]),
    ("ix_support_messages_thread_created", "support_messages", ["thread_id", "created_at"]),
    ("ix_jump_events_survey_status", "jump_events", ["survey_id", "status"]),
    ("ix_jump_events_participant_clicked", "jump_events", ["participant_id", "clicked_at"]),
    ("ix_respondent_predictions_survey_expires", "respondent_predictions", ["survey_id", "expires_at"]),
    ("ix_user_activity_events_user_created", "user_activity_events", ["user_id", "created_at"]),
    ("ix_user_activity_events_survey_type_created", "user_activity_events", ["survey_id", "event_type", "created_at"]),
]


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, table_name, columns in INDEXES:
        if not _table_exists(inspector, table_name):
            continue
        if _index_exists(inspector, table_name, index_name):
            continue
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, table_name, _columns in reversed(INDEXES):
        if not _table_exists(inspector, table_name):
            continue
        if not _index_exists(inspector, table_name, index_name):
            continue
        op.drop_index(index_name, table_name=table_name)
