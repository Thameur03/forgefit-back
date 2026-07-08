"""Add user reset-password fields

Adds:
  - reset_password_code         : String, nullable
  - reset_password_code_expires : TIMESTAMP WITH TIME ZONE, nullable

These columns were added to models/user.py but never had an explicit Alembic
migration.  Base.metadata.create_all() creates missing *tables* but does not
add new columns to existing tables, so production databases that existed before
this commit will be missing these columns, causing a 500 on
POST /auth/forgot-password.

Both columns are nullable with no server default so that existing rows are
unaffected.

This migration is safe to run on a database that already has the columns via
create_all(): the upgrade() path checks for their existence before adding them.

Revision ID: 002
Revises: 001
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Return True if the column already exists (e.g. was created by create_all)."""
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _column_exists("users", "reset_password_code"):
        op.add_column(
            "users",
            sa.Column("reset_password_code", sa.String(), nullable=True),
        )

    if not _column_exists("users", "reset_password_code_expires"):
        op.add_column(
            "users",
            sa.Column(
                "reset_password_code_expires",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _column_exists("users", "reset_password_code_expires"):
        op.drop_column("users", "reset_password_code_expires")

    if _column_exists("users", "reset_password_code"):
        op.drop_column("users", "reset_password_code")
