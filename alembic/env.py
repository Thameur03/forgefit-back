import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context

from dotenv import load_dotenv
load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import database
from models.user import User
from models.workout import Workout, WorkoutSet
from models.program import Program, ProgramDay, ProgramExercise
from models.nutrition import NutritionDayStatus, NutritionLog
from models.token import RevokedToken
from models.schedule import ScheduledWorkout
from models.analytics_event import AnalyticsEvent  # noqa: F401 — keep for autogenerate
from models.account_deletion import AccountDeletionChallenge  # noqa: F401
from models.admin_audit import AdminAuditEvent  # noqa: F401
from models.operational_event import OperationalEvent  # noqa: F401
from models.lab_insights import LabAnalysisSnapshot, LabInsightState  # noqa: F401

target_metadata = database.Base.metadata

def get_url():
    return os.getenv("DATABASE_URL", "sqlite:///./test.db")

def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
