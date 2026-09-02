"""Add canonical context and durable Lab Insights V2 state.

Revision ID: 010
Revises: 009
Create Date: 2026-09-01

All changes are additive. Existing nutrition days remain incomplete and old
schedule rows remain untrusted because neither fact can be reconstructed safely.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    for name, column_type in (
        ("timezone", sa.String(length=64)),
        ("canonical_goal", sa.String(length=64)),
        ("calorie_target", sa.Float()),
        ("protein_target_g", sa.Float()),
        ("carbs_target_g", sa.Float()),
        ("fat_target_g", sa.Float()),
    ):
        _add("users", sa.Column(name, column_type, nullable=True))

    _add("programs", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))

    _add("workouts", sa.Column("program_id", sa.Integer(), nullable=True))
    _add("workouts", sa.Column("program_day_id", sa.Integer(), nullable=True))
    _add("workouts", sa.Column("scheduled_workout_id", sa.Integer(), nullable=True))

    _add(
        "scheduled_workouts",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
    )
    _add(
        "scheduled_workouts",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    linkage_was_added = "linkage_trustworthy" not in _columns("scheduled_workouts")
    _add(
        "scheduled_workouts",
        sa.Column(
            "linkage_trustworthy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    if linkage_was_added:
        # There is no canonical workout link for historical schedule rows.
        op.execute("UPDATE scheduled_workouts SET linkage_trustworthy = false")

    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_workouts_program_id", "workouts", "programs", ["program_id"], ["id"]
        )
        op.create_foreign_key(
            "fk_workouts_program_day_id",
            "workouts",
            "program_days",
            ["program_day_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_workouts_scheduled_workout_id",
            "workouts",
            "scheduled_workouts",
            ["scheduled_workout_id"],
            ["id"],
        )

    if "ux_workouts_scheduled_workout_id" not in _indexes("workouts"):
        op.create_index(
            "ux_workouts_scheduled_workout_id",
            "workouts",
            ["scheduled_workout_id"],
            unique=True,
            postgresql_where=sa.text("scheduled_workout_id IS NOT NULL"),
            sqlite_where=sa.text("scheduled_workout_id IS NOT NULL"),
        )

    tables = _tables()
    if "nutrition_day_statuses" not in tables:
        op.create_table(
            "nutrition_day_statuses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column(
                "is_complete", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("user_id", "date", name="uq_nutrition_day_user_date"),
        )

    if "lab_insight_states" not in tables:
        op.create_table(
            "lab_insight_states",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
            ),
            sa.Column("detector_id", sa.String(length=80), nullable=False),
            sa.Column("detector_version", sa.Integer(), nullable=False),
            sa.Column("subject_key", sa.String(length=255), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_shown_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_payload", sa.JSON(), nullable=False),
            sa.UniqueConstraint(
                "user_id", "detector_id", "detector_version", "subject_key",
                name="uq_lab_insight_identity",
            ),
        )

    if "lab_analysis_snapshots" not in tables:
        op.create_table(
            "lab_analysis_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("analysis_id", sa.String(length=36), nullable=False, unique=True),
            sa.Column(
                "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
            ),
            sa.Column("analytics_version", sa.String(length=32), nullable=False),
            sa.Column("source_data_watermark", sa.String(length=64), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("data_through", sa.DateTime(timezone=True), nullable=False),
            sa.Column("stale_after", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.UniqueConstraint(
                "user_id", "analytics_version", "source_data_watermark",
                name="uq_lab_snapshot_source",
            ),
        )

    if "ix_lab_snapshots_user_generated" not in _indexes("lab_analysis_snapshots"):
        op.create_index(
            "ix_lab_snapshots_user_generated",
            "lab_analysis_snapshots",
            ["user_id", "generated_at"],
        )


def downgrade() -> None:
    tables = _tables()
    if "lab_analysis_snapshots" in tables:
        if "ix_lab_snapshots_user_generated" in _indexes("lab_analysis_snapshots"):
            op.drop_index("ix_lab_snapshots_user_generated", table_name="lab_analysis_snapshots")
        op.drop_table("lab_analysis_snapshots")
    if "lab_insight_states" in tables:
        op.drop_table("lab_insight_states")
    if "nutrition_day_statuses" in tables:
        op.drop_table("nutrition_day_statuses")

    if "ux_workouts_scheduled_workout_id" in _indexes("workouts"):
        op.drop_index("ux_workouts_scheduled_workout_id", table_name="workouts")
    if op.get_bind().dialect.name != "sqlite":
        for name in (
            "fk_workouts_scheduled_workout_id",
            "fk_workouts_program_day_id",
            "fk_workouts_program_id",
        ):
            op.drop_constraint(name, "workouts", type_="foreignkey")

    workout_columns = [
        name
        for name in ("scheduled_workout_id", "program_day_id", "program_id")
        if name in _columns("workouts")
    ]
    schedule_columns = [
        name
        for name in ("linkage_trustworthy", "completed_at", "status")
        if name in _columns("scheduled_workouts")
    ]
    user_columns = [
        name
        for name in (
            "fat_target_g",
            "carbs_target_g",
            "protein_target_g",
            "calorie_target",
            "canonical_goal",
            "timezone",
        )
        if name in _columns("users")
    ]
    if op.get_bind().dialect.name == "sqlite":
        # SQLite DROP COLUMN cannot remove columns referenced by reflected
        # foreign keys. Alembic batch mode safely recreates each local table.
        if workout_columns:
            with op.batch_alter_table("workouts") as batch:
                for name in workout_columns:
                    batch.drop_column(name)
        if schedule_columns:
            with op.batch_alter_table("scheduled_workouts") as batch:
                for name in schedule_columns:
                    batch.drop_column(name)
        if "activated_at" in _columns("programs"):
            with op.batch_alter_table("programs") as batch:
                batch.drop_column("activated_at")
        if user_columns:
            with op.batch_alter_table("users") as batch:
                for name in user_columns:
                    batch.drop_column(name)
    else:
        for name in workout_columns:
            op.drop_column("workouts", name)
        for name in schedule_columns:
            op.drop_column("scheduled_workouts", name)
        if "activated_at" in _columns("programs"):
            op.drop_column("programs", "activated_at")
        for name in user_columns:
            op.drop_column("users", name)
