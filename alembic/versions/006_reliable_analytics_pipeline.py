"""Make product analytics canonical, idempotent, and stitchable.

Revision ID: 006
Revises: 005
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    columns = _columns("analytics_events")
    if "client_event_id" not in columns:
        op.add_column(
            "analytics_events",
            sa.Column("client_event_id", sa.String(64), nullable=True),
        )
    if "occurred_at" not in columns:
        op.add_column(
            "analytics_events",
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            "UPDATE analytics_events SET occurred_at = created_at WHERE occurred_at IS NULL"
        )
        if op.get_bind().dialect.name == "sqlite":
            # SQLite needs a table rebuild for nullability changes. Alembic's
            # batch operation performs that safely for disposable/local DBs.
            with op.batch_alter_table("analytics_events") as batch_op:
                batch_op.alter_column(
                    "occurred_at",
                    existing_type=sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.func.now(),
                )
        else:
            op.alter_column(
                "analytics_events",
                "occurred_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
    if "identity_linked_at" not in columns:
        op.add_column(
            "analytics_events",
            sa.Column("identity_linked_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Canonicalize the small set of legacy aliases in-place so historical and
    # new rows use the same business definitions.
    aliases = {
        "sign_up_completed": "signup_completed",
        "workout_logged": "workout_completed",
        "workout_start_clicked": "workout_started",
        "workout_created": "workout_started",
        "exercise_added_to_workout": "exercise_added",
        "exercise_search_used": "exercise_search_performed",
        "food_search_used": "food_search_performed",
        "nutrition_tab_viewed": "nutrition_viewed",
        "programs_viewed": "program_viewed",
        "program_day_viewed": "program_viewed",
        "lab_insights_loaded": "lab_insight_generated",
        "recommendation_card_viewed": "recommendation_interacted",
        "next_action_viewed": "recommendation_interacted",
    }
    bind = op.get_bind()
    for legacy, canonical in aliases.items():
        bind.execute(
            sa.text(
                "UPDATE analytics_events SET event_name = :canonical "
                "WHERE event_name = :legacy"
            ),
            {"legacy": legacy, "canonical": canonical},
        )

    indexes = _indexes("analytics_events")
    desired = {
        "ix_analytics_events_client_event_id": (["client_event_id"], True),
        "ix_analytics_events_anonymous_id": (["anonymous_id"], False),
        "ix_analytics_events_occurred_at": (["occurred_at"], False),
        "ix_analytics_events_name_occurred": (["event_name", "occurred_at"], False),
        "ix_analytics_events_user_occurred": (["user_id", "occurred_at"], False),
        "ix_analytics_events_anon_session": (["anonymous_id", "session_id"], False),
    }
    for name, (fields, unique) in desired.items():
        if name not in indexes:
            op.create_index(name, "analytics_events", fields, unique=unique)

    for obsolete in (
        "ix_analytics_events_name_created",
        "ix_analytics_events_user_created",
    ):
        if obsolete in indexes:
            op.drop_index(obsolete, table_name="analytics_events")


def downgrade() -> None:
    indexes = _indexes("analytics_events")
    for name in (
        "ix_analytics_events_anon_session",
        "ix_analytics_events_user_occurred",
        "ix_analytics_events_name_occurred",
        "ix_analytics_events_occurred_at",
        "ix_analytics_events_anonymous_id",
        "ix_analytics_events_client_event_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="analytics_events")
    op.drop_column("analytics_events", "identity_linked_at")
    op.drop_column("analytics_events", "occurred_at")
    op.drop_column("analytics_events", "client_event_id")
