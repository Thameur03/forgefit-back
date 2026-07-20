"""Add client_request_id to workouts for idempotent creation

Adds:
  - client_request_id : VARCHAR(36), nullable

  Uniqueness is enforced by a PARTIAL index:
    CREATE UNIQUE INDEX ix_workouts_user_request_unique
      ON workouts (user_id, client_request_id)
      WHERE client_request_id IS NOT NULL

  This means:
    - Existing rows with client_request_id IS NULL are unaffected.
    - Old app versions that do not send client_request_id continue to work.
    - Any two rows belonging to the same user cannot share a non-NULL key.

  The upgrade() function guards every DDL operation with IF NOT EXISTS so it is
  safe to run on databases where Base.metadata.create_all() has already added
  the column (which create_all() does NOT do — but this is a safety net).

Revision ID: 003
Revises: 002
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Return True if *column* already exists on *table*."""
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _index_exists(index_name: str) -> bool:
    """Return True if *index_name* already exists (checked via pg_indexes)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :name"
        ),
        {"name": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    # 1. Add the column if it does not already exist.
    if not _column_exists("workouts", "client_request_id"):
        op.add_column(
            "workouts",
            sa.Column("client_request_id", sa.String(36), nullable=True),
        )

    # 2. Create the partial unique index.
    #    CREATE UNIQUE INDEX IF NOT EXISTS is not available in all PG versions;
    #    we guard with a manual existence check for maximum compatibility.
    if not _index_exists("ix_workouts_user_request_unique"):
        op.execute(
            """
            CREATE UNIQUE INDEX ix_workouts_user_request_unique
              ON workouts (user_id, client_request_id)
             WHERE client_request_id IS NOT NULL
            """
        )


def downgrade() -> None:
    if _index_exists("ix_workouts_user_request_unique"):
        op.execute("DROP INDEX ix_workouts_user_request_unique")

    if _column_exists("workouts", "client_request_id"):
        op.drop_column("workouts", "client_request_id")
