"""Add server-authoritative Apple subscription state.

Revision ID: 011
Revises: 010
Create Date: 2026-09-08

This migration only adds billing tables and indexes. It does not rewrite or
delete existing account, workout, program, schedule, nutrition, or Lab data.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "billing_identities" not in tables:
        op.create_table(
            "billing_identities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("app_account_token", sa.String(length=36), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("user_id", name="uq_billing_identity_user"),
            sa.UniqueConstraint(
                "app_account_token", name="uq_billing_identity_account_token"
            ),
        )
        op.create_index(
            "ix_billing_identities_user_id", "billing_identities", ["user_id"]
        )
        op.create_index(
            "ix_billing_identities_app_account_token",
            "billing_identities",
            ["app_account_token"],
        )

    if "apple_subscriptions" not in tables:
        op.create_table(
            "apple_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "platform",
                sa.String(length=16),
                nullable=False,
                server_default="apple",
            ),
            sa.Column("app_account_token", sa.String(length=36), nullable=False),
            sa.Column(
                "original_transaction_id", sa.String(length=128), nullable=False
            ),
            sa.Column("latest_transaction_id", sa.String(length=128), nullable=False),
            sa.Column("product_id", sa.String(length=64), nullable=False),
            sa.Column("environment", sa.String(length=24), nullable=False),
            sa.Column("storefront", sa.String(length=16), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("grace_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_signed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "original_transaction_id", name="uq_apple_subscription_original_tx"
            ),
            sa.UniqueConstraint(
                "latest_transaction_id", name="uq_apple_subscription_latest_tx"
            ),
        )
        op.create_index(
            "ix_apple_subscriptions_user_id", "apple_subscriptions", ["user_id"]
        )
        op.create_index(
            "ix_apple_subscriptions_app_account_token",
            "apple_subscriptions",
            ["app_account_token"],
        )
        op.create_index(
            "ix_apple_subscriptions_original_transaction_id",
            "apple_subscriptions",
            ["original_transaction_id"],
        )
        op.create_index(
            "ix_apple_subscriptions_latest_transaction_id",
            "apple_subscriptions",
            ["latest_transaction_id"],
        )

    if "apple_notification_events" not in tables:
        op.create_table(
            "apple_notification_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("notification_uuid", sa.String(length=64), nullable=False),
            sa.Column("notification_type", sa.String(length=64), nullable=False),
            sa.Column("subtype", sa.String(length=64), nullable=True),
            sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "notification_uuid", name="uq_apple_notification_uuid"
            ),
        )
        op.create_index(
            "ix_apple_notification_events_notification_uuid",
            "apple_notification_events",
            ["notification_uuid"],
        )


def downgrade() -> None:
    tables = _tables()
    if "apple_notification_events" in tables:
        op.drop_table("apple_notification_events")
    if "apple_subscriptions" in tables:
        op.drop_table("apple_subscriptions")
    if "billing_identities" in tables:
        op.drop_table("billing_identities")
