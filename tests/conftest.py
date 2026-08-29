"""Shared pytest isolation for the FastAPI integration suite."""

import pytest

from limiter import limiter
from tests.support import Base, app, get_db, override_get_db, test_engine
from services.operational_counters import reset_for_tests


@pytest.fixture(autouse=True)
def isolated_application_state():
    """Give every test a fresh schema and the same dependency override.

    Integration modules previously replaced the global database engine and
    FastAPI dependency override during collection. The final imported module
    therefore controlled every earlier module's requests. Recreating the
    SQLite schema per test is intentionally simple, deterministic, and immune
    to test execution order.
    """

    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    reset_for_tests()

    was_enabled = limiter.enabled
    limiter.reset()
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = was_enabled
        limiter.reset()
        app.dependency_overrides[get_db] = override_get_db
        Base.metadata.drop_all(bind=test_engine)
