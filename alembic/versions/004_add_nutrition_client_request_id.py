"""Add client_request_id to nutrition_logs for idempotent creation

Adds:
  - client_request_id : VARCHAR(36), nullable

  Uniqueness is enforced by a PARTIAL index:
    CREATE UNIQUE INDEX ix_nutrition_user_request_unique
      ON nutrition_logs (user_id, client_request_id)
      WHERE client_request_id IS NOT NULL

  This means:
    - Existing rows with client_request_id IS NULL are unaffected.
    - Old app versions that do not send client_request_id continue to work.
    - Any two rows belonging to the same user cannot share a non-NULL key.

  DEPLOYMENT ORDER (mandatory — do not skip):
    1. Backup production database.
    2. Run: alembic upgrade head
    3. Verify schema: \\d nutrition_logs (psql) or DESCRIBE nutrition_logs.
    4. Deploy backend code that references the new column.
    5. Release Flutter client that sends client_request_id.

  The upgrade() function guards every DDL operation with existence checks so
  it is safe to run multiple times (idempotent migration).

Revision ID: 004
Revises: 003
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "004"
down_revision = "003"
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
    if not _column_exists("nutrition_logs", "client_request_id"):
        op.add_column(
            "nutrition_logs",
            sa.Column("client_request_id", sa.String(36), nullable=True),
        )

    # 2. Create the partial unique index.
    if not _index_exists("ix_nutrition_user_request_unique"):
        op.execute(
            """
            CREATE UNIQUE INDEX ix_nutrition_user_request_unique
              ON nutrition_logs (user_id, client_request_id)
             WHERE client_request_id IS NOT NULL
            """
        )


def downgrade() -> None:
    if _index_exists("ix_nutrition_user_request_unique"):
        op.execute("DROP INDEX ix_nutrition_user_request_unique")

    if _column_exists("nutrition_logs", "client_request_id"):
        op.drop_column("nutrition_logs", "client_request_id")
