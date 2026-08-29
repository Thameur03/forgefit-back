"""One process-wide FastAPI application and SQLite database for tests."""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set the complete test environment before importing application modules.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["APP_ENV"] = "test"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "false"
os.environ["RESEND_API_KEY"] = ""
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ.setdefault("EXERCISEDB_API_KEY", "")
os.environ.setdefault("USDA_API_KEY", "")

import database

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)
database.engine = test_engine
database.SessionLocal = TestingSessionLocal  # type: ignore[assignment]

# Register all current ORM tables before importing the app.
import models.admin  # noqa: E402, F401
import models.analytics_event  # noqa: E402, F401
import models.account_deletion  # noqa: E402, F401
import models.admin_audit  # noqa: E402, F401
import models.food  # noqa: E402, F401
import models.food_filter  # noqa: E402, F401
import models.nutrition  # noqa: E402, F401
import models.operational_event  # noqa: E402, F401
import models.program  # noqa: E402, F401
import models.schedule  # noqa: E402, F401
import models.token  # noqa: E402, F401
import models.user  # noqa: E402, F401
import models.workout  # noqa: E402, F401

from database import Base, get_db  # noqa: E402
from main import app  # noqa: E402


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)
