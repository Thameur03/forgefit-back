"""Canonical, privacy-safe product analytics event contract.

The backend owns this contract. Clients may send a small set of legacy names,
but every newly persisted row uses a canonical event name and a server-derived
category. Event properties are deliberately narrow so generic analytics can
never become a side channel for profile, health, authentication, or free-text
data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


LEGACY_EVENT_ALIASES: dict[str, str] = {
    "sign_up_completed": "signup_completed",
    "workout_logged": "workout_completed",
    "workout_start_clicked": "workout_started",
    "workout_created": "workout_started",
    "exercise_added_to_workout": "exercise_added",
    "exercise_search_used": "exercise_search_performed",
    "food_search_used": "food_search_performed",
    "nutrition_tab_viewed": "nutrition_viewed",
    "programs_viewed": "program_viewed",
    "program_day_viewed": "program_viewed",
    "lab_insights_loaded": "insight_refreshed",
    "lab_insights_failed": "generation_failed",
    "recommendation_card_viewed": "recommendation_interacted",
    "next_action_viewed": "action_opened",
}


EVENT_CATEGORIES: dict[str, str] = {
    # App / session
    "app_opened": "session",
    "session_started": "session",
    # Signup / authentication
    "signup_started": "auth",
    "signup_step_email_completed": "auth",
    "signup_step_personal_completed": "auth",
    "signup_step_metrics_completed": "auth",
    "signup_step_fitness_completed": "auth",
    "signup_summary_viewed": "auth",
    "signup_submit_clicked": "auth",
    "signup_completed": "auth",
    "signup_failed": "auth",
    "email_verification_completed": "auth",
    "onboarding_completed": "auth",
    "login_completed": "auth",
    "login_failed": "auth",
    "logout_completed": "auth",
    # Workout
    "workout_started": "workout",
    "workout_finish_requested": "workout",
    "workout_completed": "workout",
    "workout_abandoned": "workout",
    "exercise_added": "workout",
    "exercise_search_performed": "workout",
    "personal_record_achieved": "workout",
    "workout_save_failed": "workout",
    "workout_deleted": "workout",
    # Nutrition
    "nutrition_viewed": "nutrition",
    "meal_logged": "nutrition",
    "meal_deleted": "nutrition",
    "meal_log_failed": "nutrition",
    "food_search_performed": "nutrition",
    "food_search_failed": "nutrition",
    "barcode_scan_used": "nutrition",
    "manual_food_added": "nutrition",
    "macro_goals_viewed": "nutrition",
    "micronutrients_viewed": "nutrition",
    # Programs
    "program_viewed": "program",
    "program_created": "program",
    "program_template_selected": "program",
    "program_activated": "program",
    "program_completed": "program",
    "program_changed": "program",
    "program_deleted": "program",
    # Scheduling
    "workout_scheduled": "scheduling",
    "scheduled_workout_completed": "scheduling",
    "scheduled_workout_cancelled": "scheduling",
    # Stats / insights
    "stats_viewed": "insights",
    "lab_insights_viewed": "insights",
    "insight_impression": "insights",
    "insight_opened": "insights",
    "evidence_expanded": "insights",
    "action_opened": "insights",
    "insight_refreshed": "insights",
    "generation_failed": "insights",
    # Legacy canonical names remain accepted for released clients, but V2 does
    # not emit them and admin reporting keeps them explicitly separate.
    "lab_insight_generated": "insights",
    "recommendation_interacted": "insights",
}

CANONICAL_EVENT_NAMES = frozenset(EVENT_CATEGORIES)

# These conversion milestones are committed by the backend in the same
# transaction as the underlying account state. Generic clients may not submit
# them, which prevents fabricated or retried client events from becoming the
# source of truth for signup, verification, or onboarding conversion.
SERVER_RECORDED_EVENT_NAMES = frozenset(
    {
        "signup_completed",
        "email_verification_completed",
        "onboarding_completed",
        "logout_completed",
    }
)

# Only events which can happen before a JWT exists are accepted publicly.
PUBLIC_EVENT_NAMES = frozenset(
    {
        "app_opened",
        "session_started",
        "signup_started",
        "signup_step_email_completed",
        "signup_step_personal_completed",
        "signup_step_metrics_completed",
        "signup_step_fitness_completed",
        "signup_summary_viewed",
        "signup_submit_clicked",
        "signup_failed",
        "login_failed",
    }
)

FAILURE_EVENT_NAMES = frozenset(
    {
        "signup_failed",
        "login_failed",
        "workout_save_failed",
        "meal_log_failed",
        "generation_failed",
        "food_search_failed",
    }
)

# These are the events that qualify a user as meaningfully active. App opens,
# logins, and passive screen views are intentionally excluded.
MEANINGFUL_ACTIVITY_EVENTS = frozenset(
    {
        "workout_completed",
        "meal_logged",
        "program_created",
        "program_activated",
        "program_completed",
        "program_changed",
        "workout_scheduled",
        "scheduled_workout_completed",
        "scheduled_workout_cancelled",
        "personal_record_achieved",
        "insight_opened",
        "action_opened",
        "lab_insight_generated",
        "recommendation_interacted",
    }
)


_COMMON_ERROR_PROPERTIES = frozenset({"error_code"})
_ACQUISITION_PROPERTIES = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_content", "referrer"}
)
EVENT_PROPERTY_ALLOWLIST: dict[str, frozenset[str]] = {
    "signup_started": _ACQUISITION_PROPERTIES,
    "signup_failed": _COMMON_ERROR_PROPERTIES,
    "login_failed": _COMMON_ERROR_PROPERTIES,
    "workout_completed": frozenset(
        {
            "exercise_count",
            "set_count",
            "duration_bucket",
            "restored_workout",
            "recovered_finalization",
            "scheduled",
            "program_based",
            "schedule_id",
        }
    ),
    "workout_abandoned": frozenset({"duration_bucket", "exercise_count"}),
    "workout_save_failed": _COMMON_ERROR_PROPERTIES,
    "meal_logged": frozenset(
        {"meal_type", "has_macros", "source", "catalog_food_id"}
    ),
    "meal_log_failed": _COMMON_ERROR_PROPERTIES,
    "food_search_performed": frozenset({"result_bucket", "source"}),
    "food_search_failed": _COMMON_ERROR_PROPERTIES,
    "barcode_scan_used": frozenset({"result"}),
    "manual_food_added": frozenset({"has_macros"}),
    "program_created": frozenset({"source", "template_id"}),
    "program_template_selected": frozenset({"template_id"}),
    "program_activated": frozenset({"source_template", "template_id"}),
    "program_changed": frozenset({"change_type"}),
    "workout_scheduled": frozenset({"days_ahead_bucket", "schedule_id"}),
    "scheduled_workout_completed": frozenset({"same_day", "schedule_id"}),
    "scheduled_workout_cancelled": frozenset({"schedule_id"}),
    "lab_insight_generated": frozenset(
        {"score_bucket", "has_warning", "insight_category"}
    ),
    "insight_refreshed": frozenset({"cache_status"}),
    "insight_impression": frozenset(
        {"detector_id", "lifecycle", "confidence"}
    ),
    "insight_opened": frozenset({"detector_id"}),
    "evidence_expanded": frozenset({"detector_id"}),
    "action_opened": frozenset({"detector_id"}),
    "generation_failed": _COMMON_ERROR_PROPERTIES,
    "recommendation_interacted": frozenset(
        {"score_bucket", "interaction", "insight_category"}
    ),
}

_ALLOWED_MEAL_TYPES = frozenset(
    {"breakfast", "lunch", "dinner", "snacks", "custom"}
)
_ALLOWED_SOURCES = frozenset(
    {"search", "manual", "barcode", "template", "custom", "unknown"}
)
_ALLOWED_SCORE_BUCKETS = frozenset(
    {"0_20", "21_40", "41_60", "61_80", "81_100"}
)
_ALLOWED_DURATION_BUCKETS = frozenset(
    {"under_15", "15_30", "30_45", "45_60", "60_plus"}
)
_ALLOWED_RESULT_BUCKETS = frozenset({"0", "1_5", "6_20", "21_plus"})
_ALLOWED_BARCODE_RESULTS = frozenset({"found", "not_found", "error"})
_SAFE_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")
_SAFE_ERROR = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


def canonical_event_name(value: str) -> str:
    """Return the one persisted name for a canonical or supported legacy name."""
    return LEGACY_EVENT_ALIASES.get(value, value)


def event_category(event_name: str) -> str:
    return EVENT_CATEGORIES[event_name]


def _safe_scalar(key: str, value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if key in {
            "exercise_count",
            "set_count",
            "catalog_food_id",
            "template_id",
            "schedule_id",
        }:
            return value if 0 <= value <= 1_000_000 else None
        return value if -1_000_000 <= value <= 1_000_000 else None
    if isinstance(value, float):
        return value if -1_000_000 <= value <= 1_000_000 else None
    if not isinstance(value, str) or len(value) > 64:
        return None
    if key == "meal_type":
        return value if value in _ALLOWED_MEAL_TYPES else "custom"
    if key == "source":
        return value if value in _ALLOWED_SOURCES else None
    if key == "score_bucket":
        return value if value in _ALLOWED_SCORE_BUCKETS else None
    if key == "duration_bucket":
        return value if value in _ALLOWED_DURATION_BUCKETS else None
    if key == "result_bucket":
        return value if value in _ALLOWED_RESULT_BUCKETS else None
    if key == "result":
        return value if value in _ALLOWED_BARCODE_RESULTS else None
    if key == "error_code":
        return value if _SAFE_ERROR.fullmatch(value) else None
    return value if _SAFE_SLUG.fullmatch(value) else None


def sanitize_event_properties(
    event_name: str, properties: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Keep only event-specific keys and bounded scalar values."""
    if not properties:
        return None
    allowed = EVENT_PROPERTY_ALLOWLIST.get(event_name, frozenset())
    if not allowed:
        return None
    clean: dict[str, Any] = {}
    for key in allowed:
        if key not in properties:
            continue
        value = _safe_scalar(key, properties[key])
        if value is not None:
            clean[key] = value
    return clean or None


def safe_stored_event_properties(
    event_name: str, properties: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Re-sanitize legacy rows before properties reach an admin client.

    Rows written before the event-specific ingestion contract may contain
    arbitrary keys, so stored JSON is still treated as untrusted at read time.
    """
    canonical = canonical_event_name(event_name)
    if canonical not in CANONICAL_EVENT_NAMES:
        return {}
    return sanitize_event_properties(canonical, properties) or {}
