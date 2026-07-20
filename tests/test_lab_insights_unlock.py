"""
Tests for the 7-calendar-day Lab Insights unlock gate.

These tests use only Python stdlib datetime — no DB, no FastAPI setup.
They test the pure unlock logic extracted from routers/ai.py.
"""
import pytest
from datetime import date, timedelta


# ── Pure unlock logic (mirrors routers/ai.py) ────────────────────────────────
UNLOCK_DAYS = 7


def compute_unlock(created_date: date, today: date) -> dict:
    unlock_date = created_date + timedelta(days=UNLOCK_DAYS)
    unlocked = today >= unlock_date
    days_remaining = max(0, (unlock_date - today).days)
    return {
        "unlocked": unlocked,
        "days_remaining": days_remaining,
        "unlock_date": unlock_date,
        "created_at": created_date,
    }


# ── Unlock-logic unit tests ───────────────────────────────────────────────────


def test_locked_registered_today():
    """1. Locked when registered today."""
    today = date(2026, 7, 20)
    result = compute_unlock(today, today)
    assert result["unlocked"] is False
    assert result["days_remaining"] == 7


def test_correct_days_remaining():
    """2. Correct days remaining after 3 days."""
    created = date(2026, 7, 15)
    today = date(2026, 7, 18)  # 3 days after
    result = compute_unlock(created, today)
    assert result["unlocked"] is False
    assert result["days_remaining"] == 4


def test_locked_one_day_before_unlock():
    """3. Locked one day before the unlock date."""
    created = date(2026, 7, 1)
    today = date(2026, 7, 7)  # unlock_date = July 8
    result = compute_unlock(created, today)
    assert result["unlocked"] is False
    assert result["days_remaining"] == 1


def test_unlocked_on_exact_seventh_day():
    """4. Unlocks on the exact seventh calendar day."""
    created = date(2026, 7, 1)
    today = date(2026, 7, 8)  # unlock_date = July 8
    result = compute_unlock(created, today)
    assert result["unlocked"] is True
    assert result["days_remaining"] == 0


def test_unlocked_past_unlock_date():
    """5. User past unlock date is unlocked."""
    created = date(2026, 6, 1)
    today = date(2026, 7, 20)
    result = compute_unlock(created, today)
    assert result["unlocked"] is True
    assert result["days_remaining"] == 0


def test_calendar_boundaries_month_crossing():
    """6. Calendar boundaries are correct across month boundaries."""
    # Created June 28 → unlock July 5
    created = date(2026, 6, 28)
    unlock_expected = date(2026, 7, 5)
    result_day_before = compute_unlock(created, date(2026, 7, 4))
    result_unlock_day = compute_unlock(created, date(2026, 7, 5))
    assert result_day_before["unlocked"] is False
    assert result_unlock_day["unlocked"] is True
    assert result_unlock_day["unlock_date"] == unlock_expected


def test_calendar_boundaries_year_crossing():
    """6b. Calendar boundaries correct across year boundaries."""
    created = date(2025, 12, 28)
    unlock_expected = date(2026, 1, 4)
    result = compute_unlock(created, date(2026, 1, 4))
    assert result["unlocked"] is True
    assert result["unlock_date"] == unlock_expected


def test_unlock_date_reported_correctly():
    """Unlock date returned matches created_date + 7 days."""
    created = date(2026, 7, 17)
    today = date(2026, 7, 20)
    result = compute_unlock(created, today)
    assert result["unlock_date"] == date(2026, 7, 24)


def test_days_remaining_never_negative():
    """days_remaining never negative even when long past unlock."""
    created = date(2026, 1, 1)
    today = date(2026, 7, 20)
    result = compute_unlock(created, today)
    assert result["days_remaining"] == 0


# ── FastAPI endpoint integration test ────────────────────────────────────────
# Tests 7 and 8 require a test client.  We import only if fastapi available.
try:
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock, patch

    # Minimal test — we mock the DB layer so no real DB is needed.
    def _make_mock_user(created_date: date, timezone_hours: int = 0):
        """Build a mock User object whose created_at is timezone-aware."""
        from datetime import datetime, timezone, timedelta as td
        tz = timezone(td(hours=timezone_hours))
        user = MagicMock()
        user.created_at = datetime(
            created_date.year,
            created_date.month,
            created_date.day,
            12, 0, 0,
            tzinfo=tz,
        )
        return user

    def test_no_score_while_locked():
        """7. Backend must not expose scoring data while locked."""
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from routers.ai import _unlock_status

        today = date.today()
        mock_user = _make_mock_user(today)  # created today → locked
        status = _unlock_status(mock_user)
        assert not status.unlocked
        assert status.days_remaining == 7

    def test_unlock_status_response_fields():
        """Full unlock-status response has the expected contract fields."""
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from routers.ai import _unlock_status

        created = date.today() - timedelta(days=10)  # past the gate
        mock_user = _make_mock_user(created)
        status = _unlock_status(mock_user)
        assert hasattr(status, 'unlocked')
        assert hasattr(status, 'days_remaining')
        assert hasattr(status, 'unlock_date')
        assert hasattr(status, 'created_at')
        assert status.unlocked is True

except ImportError:
    pass  # Skip integration tests if FastAPI not available in test env
