from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ── Forbidden property keys ───────────────────────────────────────────────────
_FORBIDDEN_KEYS = {
    "password", "hashed_password", "token", "access_token", "refresh_token",
    "email", "food_name", "exercise_name", "program_name", "workout_notes",
    "notes", "body_weight", "weight_kg", "height_cm", "body_measurements",
    "bmi", "ai_text", "ai_recommendation", "exception", "error_detail",
    "traceback", "stack_trace",
}

# ── Allowed values for safe string properties ─────────────────────────────────
_ALLOWED_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snacks", "custom"}
_ALLOWED_SOURCES = {"search", "manual"}
_ALLOWED_SCORE_BUCKETS = {"0_20", "21_40", "41_60", "61_80", "81_100"}
_ALLOWED_DURATION_BUCKETS = {"under_15", "15_30", "30_45", "45_60", "60_plus"}
_ALLOWED_COUNT_BUCKETS = {"1_3", "4_7", "8_plus"}

# ── All valid event names ─────────────────────────────────────────────────────
_ALL_VALID_EVENTS = {
    # App / session
    "app_opened", "session_started",
    # Auth
    "signup_started", "signup_step_email_completed", "signup_step_personal_completed",
    "signup_step_metrics_completed", "signup_step_fitness_completed",
    "signup_summary_viewed", "signup_submit_clicked", "sign_up_completed", "signup_failed",
    "login_completed", "login_failed", "logout_completed",
    # Workout
    "workout_start_clicked", "workout_created", "exercise_search_used",
    "exercise_added_to_workout", "finish_workout_clicked", "workout_logged",
    "workout_save_failed", "workout_deleted",
    # Nutrition
    "nutrition_tab_viewed", "food_search_used", "food_selected", "add_food_clicked",
    "meal_logged", "meal_log_failed", "custom_meal_type_created", "meal_deleted",
    # Programs
    "programs_viewed", "program_created", "program_template_selected",
    "program_activated", "program_deleted", "program_day_viewed",
    # Lab / Stats
    "stats_viewed", "lab_insights_viewed", "lab_insights_loaded", "lab_insights_failed",
    "recommendation_card_viewed", "next_action_viewed",
}

# ── Events allowed via public (no-auth) endpoint ──────────────────────────────
_PUBLIC_ALLOWED_EVENTS = {
    "app_opened", "session_started",
    "signup_started", "signup_step_email_completed", "signup_step_personal_completed",
    "signup_step_metrics_completed", "signup_step_fitness_completed",
    "signup_summary_viewed", "signup_submit_clicked",
    "signup_failed", "login_failed",
}

# ── Failure event names (for errors endpoint) ─────────────────────────────────
FAILURE_EVENT_NAMES = {
    "signup_failed", "login_failed", "workout_save_failed",
    "meal_log_failed", "lab_insights_failed", "food_search_failed",
}


def _sanitize_properties(raw: Optional[dict]) -> Optional[dict]:
    """Strip forbidden keys and validate safe values. Returns cleaned dict."""
    if not raw:
        return None
    clean = {}
    for k, v in raw.items():
        if k in _FORBIDDEN_KEYS:
            continue
        # Validate string allowlists for known sensitive fields
        if k == "meal_type" and v not in _ALLOWED_MEAL_TYPES:
            clean[k] = "custom"
            continue
        if k == "source" and v not in _ALLOWED_SOURCES:
            continue
        if k == "score_bucket" and v not in _ALLOWED_SCORE_BUCKETS:
            continue
        if k == "duration_bucket" and v not in _ALLOWED_DURATION_BUCKETS:
            continue
        # Limit string length to avoid large payloads
        if isinstance(v, str) and len(v) > 100:
            continue
        # Only allow safe scalar types
        if isinstance(v, (str, int, float, bool)) or v is None:
            clean[k] = v
    return clean if clean else None


# ── Ingestion schemas ─────────────────────────────────────────────────────────

class AnalyticsEventCreate(BaseModel):
    event_name: str = Field(..., max_length=100)
    event_category: Optional[str] = Field(None, max_length=50)
    screen: Optional[str] = Field(None, max_length=100)
    properties: Optional[dict[str, Any]] = None
    platform: Optional[str] = Field(None, max_length=20)
    app_version: Optional[str] = Field(None, max_length=20)
    session_id: Optional[str] = Field(None, max_length=64)

    @field_validator("event_name")
    @classmethod
    def validate_event_name(cls, v: str) -> str:
        if v not in _ALL_VALID_EVENTS:
            raise ValueError(f"Unknown event_name: {v}")
        return v

    @field_validator("properties", mode="before")
    @classmethod
    def sanitize_props(cls, v):
        return _sanitize_properties(v)


class PublicAnalyticsEventCreate(BaseModel):
    event_name: str = Field(..., max_length=100)
    event_category: Optional[str] = Field(None, max_length=50)
    screen: Optional[str] = Field(None, max_length=100)
    properties: Optional[dict[str, Any]] = None
    platform: Optional[str] = Field(None, max_length=20)
    app_version: Optional[str] = Field(None, max_length=20)
    anonymous_id: str = Field(..., min_length=1, max_length=64)
    session_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("event_name")
    @classmethod
    def validate_event_name(cls, v: str) -> str:
        if v not in _PUBLIC_ALLOWED_EVENTS:
            raise ValueError(f"Event '{v}' is not allowed on public endpoint")
        return v

    @field_validator("properties", mode="before")
    @classmethod
    def sanitize_props(cls, v):
        return _sanitize_properties(v)


# ── Admin analytics response schemas ─────────────────────────────────────────

class EventCountItem(BaseModel):
    event_name: str
    count: int


class AnalyticsOverviewResponse(BaseModel):
    total_events: int
    unique_users: int
    average_events_per_user: float
    active_users_24h: int
    top_events: list[EventCountItem]


class SignupFunnelResponse(BaseModel):
    total: int
    step_1: int
    step_2: int
    step_3: int
    step_4: int
    step_5: int
    completed: int
    conversion_rate: float


class FeatureUsageItem(BaseModel):
    event_name: str
    count: int
    unique_users: int


class RetentionResponse(BaseModel):
    daily_active_users: int
    weekly_active_users: int
    monthly_active_users: int
    stickiness: float


class TopUserItem(BaseModel):
    user_id: int
    email: str
    event_count: int


class RecentEventItem(BaseModel):
    id: int
    event_name: str
    user_id: Optional[int]
    email: str
    timestamp: str
    metadata: dict


class ErrorEventItem(BaseModel):
    event_name: str
    error_code: Optional[str]
    count: int
    unique_users: int
