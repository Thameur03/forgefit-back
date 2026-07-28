"""
conftest.py — pytest configuration for the backend test suite.

Problem: main.py imports models.analytics_event (uses JSONB) and calls
Base.metadata.create_all() at module level. SQLite cannot create a JSONB column.

Solution: patch database.Base.metadata.create_all with a no-op at the system
level BEFORE main.py is ever imported, then manually create only the
non-JSONB tables we need for testing.

This conftest must be in the tests/ directory so pytest loads it first.
"""
import os
import sys
from unittest.mock import patch

# ── Force test environment BEFORE any backend module is imported ──────────────
TEST_DB = "sqlite:///./test_password_reset.db"
os.environ["DATABASE_URL"] = TEST_DB
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "false"
os.environ["RESEND_API_KEY"] = ""

# Add backend root to sys.path so `from main import app` works in tests.
BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, os.path.abspath(BACKEND_ROOT))
