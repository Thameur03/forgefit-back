from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class DashboardResponse(BaseModel):
    total_users: int
    admin_users: int
    normal_users: int
    recently_active_users: int
    logged_out_users: int
    total_program_templates: int
    total_foods: int
    total_food_categories: int
    total_micronutrients: int


class AdminUserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    created_at: datetime
    last_login_at: Optional[datetime] = None
    last_logout_at: Optional[datetime] = None
    fitness_level: Optional[str] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None

    class Config:
        from_attributes = True


class UpdateRoleBody(BaseModel):
    role: str  # "user" or "admin"
