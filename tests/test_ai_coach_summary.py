"""
Unit tests for AICoachEngine data-state-aware summary and next-best-action logic.

These tests exercise the pure Python logic with no DB or FastAPI setup.
They validate the five data states: no_data, workout_only, nutrition_only,
limited, sufficient.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ai_coach import AICoachEngine

# ── Forbidden phrases that must never appear in low-data summaries ────────────
_FORBIDDEN = [
    "mostly on track",
    "well balanced",
    "mostly on",
    "on track",
    "your week is balanced",
    "keep crushing",
    "doing great",
]


def _make_features(workouts: int, logged_days: int) -> dict:
    """Minimal features dict sufficient for _derive_data_state and summary."""
    return {
        "workouts_this_period": workouts,
        "workouts_previous_period": 0,
        "weekly_volume_kg": 0.0,
        "previous_weekly_volume_kg": 0.0,
        "volume_change_percent": None,
        "total_sets": 0,
        "sets_by_muscle": {},
        "top_muscle": "",
        "top_muscle_sets": 0,
        "top_muscle_share_percent": 0.0,
        "lowest_muscle": "",
        "active_program": None,
        "adherence_percent": None,
        "logged_days": logged_days,
        "logging_consistency_percent": (logged_days / 7) * 100,
        "average_daily_calories": 0.0,
        "average_daily_protein_g": 0.0,
        "protein_per_kg": None,
        "calorie_cv": None,
        "missing_data": [],
        "has_bodyweight": False,
    }


# ── State A — No data ─────────────────────────────────────────────────────────

class TestStateANoData:
    def test_data_state_is_no_data(self):
        f = _make_features(0, 0)
        assert AICoachEngine._derive_data_state(f) == "no_data"

    def test_summary_never_says_on_track(self):
        f = _make_features(0, 0)
        summary = AICoachEngine._generate_summary_text("no_data", f, "Moderate")
        low = summary.lower()
        for phrase in _FORBIDDEN:
            assert phrase not in low, (
                f"Forbidden phrase '{phrase}' found in no-data summary: {summary}"
            )

    def test_summary_mentions_no_data(self):
        f = _make_features(0, 0)
        summary = AICoachEngine._generate_summary_text("no_data", f, "Good")
        assert "no workouts" in summary.lower() or "no workout" in summary.lower()

    def test_next_action_prompts_workout_log(self):
        f = _make_features(0, 0)
        action = AICoachEngine._generate_next_best_action([], "no_data", f)
        assert action is not None
        assert "workout" in action.lower() or "log" in action.lower()


# ── State B — Workout only ────────────────────────────────────────────────────

class TestStateBWorkoutOnly:
    def test_data_state_is_workout_only(self):
        f = _make_features(3, 0)
        assert AICoachEngine._derive_data_state(f) == "workout_only"

    def test_summary_mentions_missing_nutrition(self):
        f = _make_features(3, 0)
        summary = AICoachEngine._generate_summary_text("workout_only", f, "Good")
        assert "nutrition" in summary.lower()

    def test_next_action_prompts_meal_log(self):
        f = _make_features(3, 0)
        action = AICoachEngine._generate_next_best_action([], "workout_only", f)
        assert action is not None
        assert "meal" in action.lower() or "nutrition" in action.lower()


# ── State C — Nutrition only ──────────────────────────────────────────────────

class TestStateCNutritionOnly:
    def test_data_state_is_nutrition_only(self):
        f = _make_features(0, 5)
        assert AICoachEngine._derive_data_state(f) == "nutrition_only"

    def test_summary_mentions_missing_workouts(self):
        f = _make_features(0, 5)
        summary = AICoachEngine._generate_summary_text("nutrition_only", f, "Moderate")
        assert "workout" in summary.lower()

    def test_next_action_prompts_workout_log(self):
        f = _make_features(0, 5)
        action = AICoachEngine._generate_next_best_action([], "nutrition_only", f)
        assert action is not None
        assert "workout" in action.lower()


# ── State D — Limited mixed ───────────────────────────────────────────────────

class TestStateDLimited:
    def test_data_state_limited_one_workout(self):
        f = _make_features(1, 4)
        assert AICoachEngine._derive_data_state(f) == "limited"

    def test_data_state_limited_two_logged_days(self):
        f = _make_features(2, 2)
        assert AICoachEngine._derive_data_state(f) == "limited"

    def test_summary_uses_cautious_language(self):
        f = _make_features(1, 4)
        summary = AICoachEngine._generate_summary_text("limited", f, "Good")
        # Must not sound confident
        for phrase in _FORBIDDEN:
            assert phrase not in summary.lower()
        assert "early" in summary.lower() or "limited" in summary.lower()

    def test_next_action_prompts_more_logging(self):
        f = _make_features(1, 4)  # sparse workouts but enough logged days
        action = AICoachEngine._generate_next_best_action([], "limited", f)
        assert action is not None
        assert "workout" in action.lower()


# ── State E — Sufficient ──────────────────────────────────────────────────────

class TestStateESufficient:
    def test_data_state_sufficient(self):
        f = _make_features(3, 5)
        assert AICoachEngine._derive_data_state(f) == "sufficient"

    def test_summary_references_data(self):
        f = _make_features(3, 5)
        summary = AICoachEngine._generate_summary_text("sufficient", f, "Excellent")
        # Should mention recorded sessions or data
        assert "data" in summary.lower() or "recorded" in summary.lower() or "training" in summary.lower()

    def test_next_action_uses_recommendation_when_not_empty(self):
        from schemas.ai_coach import AICoachRecommendation
        rec = AICoachRecommendation(
            title="Increase protein",
            reason="Protein is low",
            action="Add a protein-rich meal.",
            priority="high",
            category="nutrition",
            impact=9,
        )
        f = _make_features(3, 5)
        action = AICoachEngine._generate_next_best_action([rec], "sufficient", f)
        assert action is not None
        assert "protein" in action.lower() or "meal" in action.lower()

    def test_next_action_none_when_no_recommendations(self):
        f = _make_features(3, 5)
        action = AICoachEngine._generate_next_best_action([], "sufficient", f)
        assert action is None


# ── Boundary tests ────────────────────────────────────────────────────────────

class TestBoundaries:
    @pytest.mark.parametrize("w,ld,expected", [
        (0, 0, "no_data"),
        (1, 0, "workout_only"),
        (0, 1, "nutrition_only"),
        (1, 3, "limited"),
        (2, 2, "limited"),
        (2, 3, "sufficient"),
        (5, 7, "sufficient"),
    ])
    def test_derive_data_state_boundaries(self, w, ld, expected):
        f = _make_features(w, ld)
        assert AICoachEngine._derive_data_state(f) == expected
