from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AdminUserListItem(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_verified: bool
    account_status: str
    created_at: datetime
    last_login_at: Optional[datetime] = None
    last_meaningful_activity_at: Optional[datetime] = None
    activity_state: str


class AdminUserPage(BaseModel):
    items: list[AdminUserListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class UserOverviewAccount(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    verified: bool
    verified_at: Optional[datetime] = None
    joined_at: datetime
    last_login_at: Optional[datetime] = None
    last_meaningful_activity_at: Optional[datetime] = None
    account_status: str
    latest_platform: Optional[str] = None
    latest_app_version: Optional[str] = None


class UserOverviewEngagement(BaseModel):
    completed_workouts: int
    workouts_last_7_days: int
    workouts_last_30_days: int
    nutrition_entries: int
    nutrition_logging_days: int
    meals_last_30_days: int
    active_program: Optional[dict[str, Any]] = None
    scheduled_workouts: int
    upcoming_scheduled_workouts: int
    last_workout_at: Optional[date] = None
    last_meal_at: Optional[date] = None
    adopted_features: list[str]


class UserOverviewState(BaseModel):
    deletion_challenge_active: bool
    deletion_challenge_expires_at: Optional[datetime] = None
    deletion_failed_attempts: Optional[int] = None
    revoked_token_count: int
    token_version: int


class SafeActivityItem(BaseModel):
    id: int
    event_name: str
    occurred_at: datetime
    platform: Optional[str] = None
    app_version: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class UserOverviewResponse(BaseModel):
    account: UserOverviewAccount
    engagement: UserOverviewEngagement
    account_state: UserOverviewState
    recent_activity: list[SafeActivityItem]


class UserWorkoutItem(BaseModel):
    id: int
    date: date
    duration_seconds: int
    exercise_count: int
    set_count: int


class UserNutritionDayItem(BaseModel):
    date: date
    entry_count: int
    meal_count: int


class UserProgramItem(BaseModel):
    id: int
    name: str
    is_active: bool
    source_template: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None


class UserScheduleItem(BaseModel):
    id: int
    scheduled_date: date
    program_id: int
    program_day_id: int
    created_at: datetime


class PaginatedResponse(BaseModel):
    items: list[Any]
    page: int
    page_size: int
    total: int
    total_pages: int


class PasswordConfirmation(BaseModel):
    current_password: str = Field(..., min_length=8, max_length=128)


class DeleteUserRequest(PasswordConfirmation):
    confirmation: Literal["DELETE"]


class UpdateUserRoleRequest(PasswordConfirmation):
    role: Literal["user", "admin", "superadmin"]


class UserStatusRequest(PasswordConfirmation):
    status: Literal["active", "suspended"]


class BootstrapSuperadminRequest(PasswordConfirmation):
    confirmation: Literal["MAKE_ME_SUPERADMIN"]


class AdminAuditItem(BaseModel):
    id: int
    admin_user_id: Optional[int] = None
    admin_email: Optional[str] = None
    action: str
    target_type: str
    target_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AdminAuditPage(BaseModel):
    items: list[AdminAuditItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class ServiceHealth(BaseModel):
    configured: bool
    status: str
    recent_failures: int = 0


class SystemHealthResponse(BaseModel):
    generated_at: datetime
    application: dict[str, Any]
    database: dict[str, Any]
    email: ServiceHealth
    external_services: dict[str, ServiceHealth]
    api: dict[str, Any]
    recent_failures: list[dict[str, Any]]
