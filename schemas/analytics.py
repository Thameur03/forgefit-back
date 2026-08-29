from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.analytics_events import (
    CANONICAL_EVENT_NAMES,
    FAILURE_EVENT_NAMES,
    PUBLIC_EVENT_NAMES,
    SERVER_RECORDED_EVENT_NAMES,
    canonical_event_name,
    event_category,
    sanitize_event_properties,
)


# ── Ingestion schemas ─────────────────────────────────────────────────────────

class AnalyticsEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(..., max_length=100)
    # Accepted for backwards compatibility but overwritten from the event map.
    event_category: Optional[str] = Field(None, max_length=50)
    screen: Optional[str] = Field(None, max_length=80, pattern=r"^[a-zA-Z0-9_./:-]+$")
    properties: Optional[dict[str, Any]] = None
    platform: Optional[str] = Field(None, max_length=20, pattern=r"^[a-z0-9_-]+$")
    app_version: Optional[str] = Field(None, max_length=32, pattern=r"^[a-zA-Z0-9.+_-]+$")
    anonymous_id: Optional[str] = Field(None, min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    session_id: Optional[str] = Field(None, min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    client_event_id: Optional[UUID] = None
    occurred_at: Optional[datetime] = None

    @model_validator(mode="after")
    def normalize_and_sanitize(self):
        canonical = canonical_event_name(self.event_name)
        if canonical not in CANONICAL_EVENT_NAMES:
            raise ValueError(f"Unknown event_name: {self.event_name}")
        if canonical in SERVER_RECORDED_EVENT_NAMES:
            raise ValueError(f"Event '{canonical}' is recorded by the backend only")
        self.event_name = canonical
        self.event_category = event_category(canonical)
        self.properties = sanitize_event_properties(canonical, self.properties)
        if self.occurred_at is not None:
            occurred = self.occurred_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if occurred < now - timedelta(days=7) or occurred > now + timedelta(minutes=5):
                raise ValueError("occurred_at must be within the last 7 days")
            self.occurred_at = occurred
        return self


class PublicAnalyticsEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(..., max_length=100)
    event_category: Optional[str] = Field(None, max_length=50)
    screen: Optional[str] = Field(None, max_length=80, pattern=r"^[a-zA-Z0-9_./:-]+$")
    properties: Optional[dict[str, Any]] = None
    platform: Optional[str] = Field(None, max_length=20, pattern=r"^[a-z0-9_-]+$")
    app_version: Optional[str] = Field(None, max_length=32, pattern=r"^[a-zA-Z0-9.+_-]+$")
    anonymous_id: str = Field(..., min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    session_id: str = Field(..., min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    client_event_id: Optional[UUID] = None
    occurred_at: Optional[datetime] = None

    @model_validator(mode="after")
    def normalize_and_sanitize(self):
        canonical = canonical_event_name(self.event_name)
        if canonical not in PUBLIC_EVENT_NAMES:
            raise ValueError(f"Event '{self.event_name}' is not allowed on public endpoint")
        self.event_name = canonical
        self.event_category = event_category(canonical)
        self.properties = sanitize_event_properties(canonical, self.properties)
        if self.occurred_at is not None:
            occurred = self.occurred_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if occurred < now - timedelta(days=7) or occurred > now + timedelta(minutes=5):
                raise ValueError("occurred_at must be within the last 7 days")
            self.occurred_at = occurred
        return self


class AnalyticsIdentityLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anonymous_id: str = Field(..., min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    session_id: str = Field(..., min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


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
