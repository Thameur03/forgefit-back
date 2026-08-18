"""Database-startup safety checks."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

import database
from main import _seed_food_filters
from models.food_filter import FoodFilter
from tests.support import TestingSessionLocal


def _slugs() -> list[str]:
    db = TestingSessionLocal()
    result = [slug for (slug,) in db.query(FoodFilter.slug).order_by(FoodFilter.slug)]
    db.close()
    return result


def test_default_food_filter_seed_is_idempotent():
    _seed_food_filters()
    first = _slugs()
    _seed_food_filters()
    second = _slugs()

    assert len(first) == 5
    assert second == first


def test_default_food_filter_seed_repairs_partial_state():
    db = TestingSessionLocal()
    db.add(
        FoodFilter(
            name="Fruit",
            slug="fruit",
            include_keywords=[],
            exclude_keywords=[],
        )
    )
    db.commit()
    db.close()

    _seed_food_filters()

    assert set(_slugs()) == {
        "dairy",
        "fruit",
        "high-protein",
        "meat",
        "vegan-friendly",
    }


def test_seed_does_not_hide_unrelated_integrity_errors(monkeypatch):
    fake_session = MagicMock()
    fake_session.query.return_value.all.side_effect = [[], []]
    fake_session.commit.side_effect = IntegrityError(
        "INSERT INTO food_filters ...",
        {},
        RuntimeError("simulated non-race integrity failure"),
    )
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)

    with pytest.raises(IntegrityError):
        _seed_food_filters()

    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()
