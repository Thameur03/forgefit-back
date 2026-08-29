"""Add admin audit history, safe operations telemetry, and session controls.

Revision ID: 007
Revises: 006
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def _create_index(name: str, table: str, fields: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, fields)


def upgrade() -> None:
    user_columns = _columns("users")
    if "verified_at" not in user_columns:
        op.add_column(
            "users", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.execute(
            "UPDATE users SET verified_at = created_at "
            "WHERE is_verified = true AND verified_at IS NULL"
        )
    if "account_status" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "account_status", sa.String(20), server_default="active", nullable=False
            ),
        )
    if "suspended_at" not in user_columns:
        op.add_column(
            "users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "token_version" not in user_columns:
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        )

    if not _table_exists("admin_audit_events"):
        op.create_table(
            "admin_audit_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("admin_user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("target_type", sa.String(50), nullable=False),
            sa.Column("target_id", sa.String(100), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["admin_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_admin_audit_events_id", "admin_audit_events", ["id"])
        op.create_index(
            "ix_admin_audit_events_admin_user_id",
            "admin_audit_events",
            ["admin_user_id"],
        )
        op.create_index(
            "ix_admin_audit_events_action", "admin_audit_events", ["action"]
        )
        op.create_index(
            "ix_admin_audit_events_target_type",
            "admin_audit_events",
            ["target_type"],
        )
        op.create_index(
            "ix_admin_audit_events_created_at",
            "admin_audit_events",
            ["created_at"],
        )
        op.create_index(
            "ix_admin_audit_action_created",
            "admin_audit_events",
            ["action", "created_at"],
        )

    if not _table_exists("operational_events"):
        op.create_table(
            "operational_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("event_name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for field in ("id", "category", "event_name", "status", "created_at"):
            op.create_index(
                f"ix_operational_events_{field}", "operational_events", [field]
            )
        op.create_index(
            "ix_operational_event_created",
            "operational_events",
            ["event_name", "created_at"],
        )

    # High-value query paths used by paginated operations and product metrics.
    _create_index("ix_users_created_at", "users", ["created_at"])
    _create_index("ix_users_role_verified", "users", ["role", "is_verified"])
    _create_index("ix_workouts_user_date", "workouts", ["user_id", "date"])
    _create_index(
        "ix_nutrition_logs_user_date", "nutrition_logs", ["user_id", "date"]
    )
    _create_index("ix_programs_user_active", "programs", ["user_id", "is_active"])
    _create_index(
        "ix_scheduled_workouts_user_date",
        "scheduled_workouts",
        ["user_id", "scheduled_date"],
    )


def downgrade() -> None:
    for name, table in (
        ("ix_scheduled_workouts_user_date", "scheduled_workouts"),
        ("ix_programs_user_active", "programs"),
        ("ix_nutrition_logs_user_date", "nutrition_logs"),
        ("ix_workouts_user_date", "workouts"),
        ("ix_users_role_verified", "users"),
        ("ix_users_created_at", "users"),
    ):
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    if _table_exists("operational_events"):
        op.drop_table("operational_events")
    if _table_exists("admin_audit_events"):
        op.drop_table("admin_audit_events")
    for column in ("token_version", "suspended_at", "account_status", "verified_at"):
        if column in _columns("users"):
            op.drop_column("users", column)
