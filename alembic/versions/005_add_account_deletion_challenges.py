"""Add hashed public account-deletion challenges.

Revision ID: 005
Revises: 004
Create Date: 2026-08-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    return inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if _table_exists("account_deletion_challenges"):
        return
    op.create_table(
        "account_deletion_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_account_deletion_challenges_id",
        "account_deletion_challenges",
        ["id"],
    )


def downgrade() -> None:
    if not _table_exists("account_deletion_challenges"):
        return
    op.drop_index(
        "ix_account_deletion_challenges_id",
        table_name="account_deletion_challenges",
    )
    op.drop_table("account_deletion_challenges")
