"""Track finalized workouts separately from start-created draft shells.

Revision ID: 008
Revises: 007
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "completed_at" not in _columns("workouts"):
        op.add_column(
            "workouts",
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "completion_inferred" not in _columns("workouts"):
        op.add_column(
            "workouts",
            sa.Column(
                "completion_inferred",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "completed_at" in _columns("workouts"):
        # Older rows have no explicit completion state. Positive final duration
        # is the conservative signal left by the existing finalization PUT.
        # Zero-duration rows remain drafts rather than being fabricated as
        # completed. The admin API reports this historical limitation.
        op.execute(
            "UPDATE workouts SET completed_at = date, "
            "completion_inferred = true "
            "WHERE duration_seconds > 0 AND completed_at IS NULL"
        )
    if "ix_workouts_completed_date" not in _indexes("workouts"):
        op.create_index(
            "ix_workouts_completed_date",
            "workouts",
            ["completed_at", "date"],
        )


def downgrade() -> None:
    if "ix_workouts_completed_date" in _indexes("workouts"):
        op.drop_index("ix_workouts_completed_date", table_name="workouts")
    if "completed_at" in _columns("workouts"):
        op.drop_column("workouts", "completed_at")
    if "completion_inferred" in _columns("workouts"):
        op.drop_column("workouts", "completion_inferred")
