"""Enforce race-safe uniqueness for admin-managed content.

Revision ID: 009
Revises: 008
Create Date: 2026-08-28

The migration never deletes or rewrites catalog content. If pre-existing
duplicates are found it stops with a precise error so they can be reviewed
manually before retrying the migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _indexes(table: str) -> dict[str, bool]:
    return {
        index["name"]: bool(index.get("unique"))
        for index in inspect(op.get_bind()).get_indexes(table)
    }


def _assert_no_duplicates(table: str, fields: tuple[str, ...]) -> None:
    columns = ", ".join(fields)
    duplicate = op.get_bind().execute(
        sa.text(
            f"SELECT {columns} FROM {table} "
            f"GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        joined = ", ".join(fields)
        raise RuntimeError(
            f"Cannot enforce uniqueness on {table} ({joined}): "
            "pre-existing duplicates require manual review"
        )


def _create_unique_index(
    name: str,
    table: str,
    fields: tuple[str, ...],
) -> None:
    indexes = _indexes(table)
    if name in indexes:
        if not indexes[name]:
            raise RuntimeError(
                f"Cannot create unique index {name}: a non-unique index "
                "already uses that name"
            )
        return
    _assert_no_duplicates(table, fields)
    op.create_index(name, table, list(fields), unique=True)


def upgrade() -> None:
    _create_unique_index(
        "ux_food_micronutrients_food_nutrient",
        "food_micronutrients",
        ("food_id", "micronutrient_id"),
    )
    _create_unique_index(
        "ux_program_template_days_number",
        "program_template_days",
        ("template_id", "day_number"),
    )
    _create_unique_index(
        "ux_program_template_days_order",
        "program_template_days",
        ("template_id", "order_index"),
    )
    _create_unique_index(
        "ux_program_template_exercises_order",
        "program_template_exercises",
        ("day_id", "order_index"),
    )

    # The API already passes is_active=false explicitly. Align the production
    # database default as defense in depth; SQLite cannot alter defaults in
    # place and is used only by isolated tests.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column(
            "program_templates",
            "is_active",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )


def downgrade() -> None:
    for name, table in (
        (
            "ux_program_template_exercises_order",
            "program_template_exercises",
        ),
        ("ux_program_template_days_order", "program_template_days"),
        ("ux_program_template_days_number", "program_template_days"),
        (
            "ux_food_micronutrients_food_nutrient",
            "food_micronutrients",
        ),
    ):
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column(
            "program_templates",
            "is_active",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )
