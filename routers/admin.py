from datetime import date, datetime, timedelta, timezone
import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.email import email_delivery_configured, send_verification_email
from auth.otp import generate_numeric_otp
from auth.utils import (
    get_current_admin,
    get_current_superadmin,
    hash_password,
    verify_password,
)
from database import get_db
from models.user import User
from models.admin import ProgramTemplate, ProgramTemplateDay, ProgramTemplateExercise
from models.food import FoodCategory, Food, Micronutrient, FoodMicronutrient
from models.admin_audit import AdminAuditEvent
from models.analytics_event import AnalyticsEvent
from models.account_deletion import AccountDeletionChallenge
from models.nutrition import NutritionLog
from models.operational_event import OperationalEvent
from models.program import Program
from models.schedule import ScheduledWorkout
from models.token import RevokedToken
from models.workout import Workout, WorkoutSet
from routers.account import _delete_user_owned_records

from schemas.admin import DashboardResponse, AdminUserResponse
from schemas.admin_operations import (
    AdminAuditPage,
    AdminUserPage,
    BootstrapSuperadminRequest,
    DeleteUserRequest,
    PaginatedResponse,
    PasswordConfirmation,
    SystemHealthResponse,
    UpdateUserRoleRequest,
    UserOverviewResponse,
    UserStatusRequest,
)
from schemas.program_template import (
    ProgramTemplateCreate, ProgramTemplateUpdate, ProgramTemplateResponse,
    ProgramTemplateSummary, ProgramTemplateDayCreate, ProgramTemplateDayUpdate,
    ProgramTemplateDaySchema, ProgramTemplateExerciseCreate,
    ProgramTemplateExerciseUpdate, ProgramTemplateExerciseSchema,
)
from schemas.food_admin import (
    FoodCategoryCreate, FoodCategoryUpdate, FoodCategoryResponse,
    FoodCreate, FoodUpdate, FoodResponse,
    MicronutrientCreate, MicronutrientUpdate, MicronutrientResponse,
    FoodMicronutrientCreate, FoodMicronutrientUpdate, FoodMicronutrientResponse,
)
from services.admin_operations import add_admin_audit_event, add_operational_event
from services.admin_users import (
    page_payload,
    paginated_users,
    require_user,
    user_overview,
)
from services.system_health import system_health
from services.admin_metrics import active_counts_at
from services.analytics_events import safe_stored_event_properties

router = APIRouter()


def _flush_admin_content(db: Session, detail: str) -> None:
    """Flush content changes without leaking a raw database constraint error."""
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail) from exc


def _commit_admin_content(db: Session, detail: str) -> None:
    """Commit content and audit rows atomically, mapping races to HTTP 409."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail) from exc


# ═══════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════

@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Return admin dashboard statistics."""
    now = datetime.now(timezone.utc)

    admin_users = db.query(User).filter(User.role.in_({"admin", "superadmin"})).count()
    normal_users = db.query(User).filter(User.role == "user").count()
    total_users = normal_users
    verified_users = db.query(User).filter(User.role == "user", User.is_verified.is_(True)).count()
    unverified_users = normal_users - verified_users

    recently_active_users, _, _ = active_counts_at(db, now.date())
    # Kept only for backwards response compatibility. Logout timestamps do not
    # identify a useful product-health cohort.
    logged_out_users = 0

    total_program_templates = db.query(ProgramTemplate).filter(ProgramTemplate.is_active.is_(True)).count()
    total_foods = db.query(Food).filter(Food.is_active.is_(True)).count()
    total_food_categories = db.query(FoodCategory).count()
    total_micronutrients = db.query(Micronutrient).count()

    return DashboardResponse(
        total_users=total_users,
        admin_users=admin_users,
        normal_users=normal_users,
        verified_users=verified_users,
        unverified_users=unverified_users,
        recently_active_users=recently_active_users,
        logged_out_users=logged_out_users,
        total_program_templates=total_program_templates,
        total_foods=total_foods,
        total_food_categories=total_food_categories,
        total_micronutrients=total_micronutrients,
    )


# ═══════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════════════════

def _require_password(admin: User, password: str) -> None:
    if not verify_password(password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )


@router.post("/security/bootstrap-superadmin", response_model=AdminUserResponse)
def bootstrap_superadmin(
    body: BootstrapSuperadminRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Explicitly establish the first superadmin without guessing the owner."""
    _require_password(admin, body.current_password)
    if db.query(User.id).filter(User.role == "superadmin").first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A superadmin already exists",
        )
    previous_role = admin.role
    admin.role = "superadmin"
    add_admin_audit_event(
        db,
        admin=admin,
        action="admin.bootstrap_superadmin",
        target_type="user",
        target_id=admin.id,
        metadata={"previous_role": previous_role},
    )
    db.commit()
    db.refresh(admin)
    return admin


@router.get("/users", response_model=AdminUserPage)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    role: Optional[str] = Query(None),
    verified: Optional[bool] = Query(None),
    account_status: Optional[str] = Query(None),
    signup_start: Optional[date] = Query(None),
    signup_end: Optional[date] = Query(None),
    last_active_start: Optional[date] = Query(None),
    last_active_end: Optional[date] = Query(None),
    activity_state: Optional[str] = Query(None),
    sort_by: str = Query("joined"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return paginated_users(
        db,
        page=page,
        page_size=page_size,
        search=search,
        role=role,
        verified=verified,
        account_status=account_status,
        signup_start=signup_start,
        signup_end=signup_end,
        last_active_start=last_active_start,
        last_active_end=last_active_end,
        state=activity_state,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Return a single user by ID."""
    return require_user(db, user_id)


@router.get("/users/{user_id}/overview", response_model=UserOverviewResponse)
def get_user_overview(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return user_overview(db, require_user(db, user_id))


@router.get("/users/{user_id}/workouts", response_model=PaginatedResponse)
def get_user_workouts(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    require_user(db, user_id)
    query = db.query(Workout).filter(
        Workout.user_id == user_id,
        Workout.completed_at.is_not(None),
    )
    total = query.count()
    rows = (
        query.order_by(Workout.date.desc(), Workout.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    workout_ids = [row.id for row in rows]
    aggregates = {
        row.workout_id: (row.exercise_count, row.set_count)
        for row in (
            db.query(
                WorkoutSet.workout_id,
                func.count(WorkoutSet.id).label("exercise_count"),
                func.coalesce(func.sum(WorkoutSet.sets), 0).label("set_count"),
            )
            .filter(WorkoutSet.workout_id.in_(workout_ids))
            .group_by(WorkoutSet.workout_id)
            .all()
            if workout_ids
            else []
        )
    }
    return page_payload(
        [
            {
                "id": row.id,
                "date": row.date,
                "duration_seconds": max(0, row.duration_seconds or 0),
                "exercise_count": aggregates.get(row.id, (0, 0))[0],
                "set_count": aggregates.get(row.id, (0, 0))[1],
            }
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/users/{user_id}/nutrition", response_model=PaginatedResponse)
def get_user_nutrition(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    require_user(db, user_id)
    grouped = (
        db.query(
            NutritionLog.date,
            func.count(NutritionLog.id).label("entry_count"),
            func.count(func.distinct(NutritionLog.meal_name)).label("meal_count"),
        )
        .filter(NutritionLog.user_id == user_id)
        .group_by(NutritionLog.date)
    )
    total = grouped.count()
    rows = (
        grouped.order_by(NutritionLog.date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [
            {
                "date": row.date,
                "entry_count": row.entry_count,
                "meal_count": row.meal_count,
            }
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/users/{user_id}/programs", response_model=PaginatedResponse)
def get_user_programs(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    require_user(db, user_id)
    query = db.query(Program).filter(Program.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(Program.is_active.desc(), Program.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [
            {
                "id": row.id,
                "name": row.name,
                "is_active": row.is_active,
                "source_template": row.source_template,
                "weeks": row.weeks,
                "days_per_week": row.days_per_week,
            }
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/users/{user_id}/schedule", response_model=PaginatedResponse)
def get_user_schedule(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    require_user(db, user_id)
    query = db.query(ScheduledWorkout).filter(ScheduledWorkout.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(ScheduledWorkout.scheduled_date.desc(), ScheduledWorkout.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [
            {
                "id": row.id,
                "scheduled_date": row.scheduled_date,
                "program_id": row.program_id,
                "program_day_id": row.program_day_id,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/users/{user_id}/events", response_model=PaginatedResponse)
def get_user_events(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    require_user(db, user_id)
    query = db.query(AnalyticsEvent).filter(AnalyticsEvent.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [
            {
                "id": row.id,
                "event_name": row.event_name,
                "occurred_at": row.occurred_at,
                "platform": row.platform,
                "app_version": row.app_version,
                "properties": safe_stored_event_properties(
                    row.event_name, row.properties
                ),
            }
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.put("/users/{user_id}/role", response_model=AdminUserResponse)
def update_user_role(
    user_id: int,
    body: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superadmin),
):
    _require_password(admin, body.current_password)
    user = require_user(db, user_id)
    if user.id == admin.id and body.role != admin.role:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot change your own role")
    if user.role == "superadmin" and body.role != "superadmin":
        superadmin_count = db.query(User.id).filter(User.role == "superadmin").count()
        if superadmin_count <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot demote the last superadmin")
    previous_role = user.role
    user.role = body.role
    add_admin_audit_event(
        db,
        admin=admin,
        action="user.role_changed",
        target_type="user",
        target_id=user.id,
        metadata={"from_role": previous_role, "to_role": body.role},
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    body: DeleteUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superadmin),
):
    """
    Delete a user and all related records in the correct FK order.
    Prevents deleting your own account.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own admin account",
        )

    _require_password(admin, body.current_password)
    user = require_user(db, user_id)

    try:
        add_admin_audit_event(
            db,
            admin=admin,
            action="user.deleted",
            target_type="user",
            target_id=user.id,
            metadata={"target_role": user.role},
        )
        _delete_user_owned_records(db, user)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User deletion failed; no data was deleted",
        ) from exc

    return {"message": f"User {user_id} deleted successfully"}


@router.put("/users/{user_id}/status", response_model=AdminUserResponse)
def update_user_status(
    user_id: int,
    body: UserStatusRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superadmin),
):
    _require_password(admin, body.current_password)
    user = require_user(db, user_id)
    if user.id == admin.id and body.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot suspend your own account")
    previous = user.account_status
    user.account_status = body.status
    user.suspended_at = datetime.now(timezone.utc) if body.status == "suspended" else None
    if previous != body.status:
        user.token_version += 1
    add_admin_audit_event(
        db,
        admin=admin,
        action="user.suspended" if body.status == "suspended" else "user.reactivated",
        target_type="user",
        target_id=user.id,
        metadata={"previous_status": previous},
    )
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/revoke-sessions")
def revoke_user_sessions(
    user_id: int,
    body: PasswordConfirmation,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superadmin),
):
    _require_password(admin, body.current_password)
    user = require_user(db, user_id)
    user.token_version += 1
    add_admin_audit_event(
        db,
        admin=admin,
        action="user.sessions_revoked",
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    return {"message": "All user sessions revoked"}


@router.post("/users/{user_id}/resend-verification")
def admin_resend_verification(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = require_user(db, user_id)
    if user.is_verified:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already verified")
    if not email_delivery_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email provider is not configured")
    code = generate_numeric_otp()
    user.verification_code = hash_password(code)
    user.verification_code_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    if not send_verification_email(user.email, code):
        db.rollback()
        add_operational_event(
            db,
            category="email",
            event_name="admin_verification_email_delivery",
            status="failed",
            error_code="provider_rejected",
        )
        db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Verification email was not delivered")
    add_operational_event(
        db,
        category="email",
        event_name="admin_verification_email_delivery",
        status="succeeded",
    )
    add_admin_audit_event(
        db,
        admin=admin,
        action="user.verification_resent",
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    return {"message": "Verification email sent"}


# ═══════════════════════════════════════════════════════════
# PROGRAM TEMPLATES
# ═══════════════════════════════════════════════════════════

@router.get("/program-templates", response_model=PaginatedResponse)
def list_program_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    is_active: Optional[bool] = Query(None),
    difficulty: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(ProgramTemplate)
    if search:
        query = query.filter(ProgramTemplate.name.ilike(f"%{search.strip()}%"))
    if is_active is not None:
        query = query.filter(ProgramTemplate.is_active.is_(is_active))
    if difficulty:
        if difficulty not in {"beginner", "intermediate", "advanced"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid difficulty")
        query = query.filter(ProgramTemplate.difficulty == difficulty)
    total = query.count()
    rows = (
        query.order_by(ProgramTemplate.updated_at.desc(), ProgramTemplate.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [ProgramTemplateSummary.model_validate(row).model_dump() for row in rows]
    return page_payload(items, page=page, page_size=page_size, total=total)


@router.post("/program-templates", response_model=ProgramTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_program_template(
    data: ProgramTemplateCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if data.is_active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Create the template as a draft, configure its days and exercises, then publish it",
        )
    template = ProgramTemplate(**data.model_dump())
    db.add(template)
    _flush_admin_content(db, "Template conflicts with existing content")
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.created",
        target_type="program_template",
        target_id=template.id,
        metadata={"published": False},
    )
    _commit_admin_content(db, "Template conflicts with existing content")
    db.refresh(template)
    return template


@router.get("/program-templates/{template_id}", response_model=ProgramTemplateResponse)
def get_program_template(
    template_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


@router.put("/program-templates/{template_id}", response_model=ProgramTemplateResponse)
def update_program_template(
    template_id: int,
    data: ProgramTemplateUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active") is True and not template.is_active:
        if not template.days or any(not day.exercises for day in template.days):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A template needs at least one configured exercise on every day before publishing",
            )
    for field, value in changes.items():
        setattr(template, field, value)
    add_admin_audit_event(
        db,
        admin=admin,
        action=(
            "program_template.published"
            if changes.get("is_active") is True
            else "program_template.hidden"
            if changes.get("is_active") is False
            else "program_template.updated"
        ),
        target_type="program_template",
        target_id=template.id,
        metadata={"field_count": len(changes)},
    )
    _commit_admin_content(db, "Template conflicts with existing content")
    db.refresh(template)
    return template


@router.delete("/program-templates/{template_id}")
def delete_program_template(
    template_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.deleted",
        target_type="program_template",
        target_id=template.id,
    )
    db.delete(template)
    _commit_admin_content(db, "Template is still referenced and cannot be deleted")
    return {"message": "Template deleted"}


# Template Days

@router.post("/program-templates/{template_id}/days", response_model=ProgramTemplateDaySchema, status_code=status.HTTP_201_CREATED)
def add_template_day(
    template_id: int,
    data: ProgramTemplateDayCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    duplicate = db.query(ProgramTemplateDay.id).filter(
        ProgramTemplateDay.template_id == template_id,
        (
            (ProgramTemplateDay.day_number == data.day_number)
            | (ProgramTemplateDay.order_index == data.order_index)
        ),
    ).first()
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Day number and order must be unique within a template")
    day = ProgramTemplateDay(template_id=template_id, **data.model_dump())
    db.add(day)
    _flush_admin_content(
        db, "Day number and order must be unique within a template"
    )
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.day_created",
        target_type="program_template_day",
        target_id=day.id,
        metadata={"template_id": template_id},
    )
    _commit_admin_content(
        db, "Day number and order must be unique within a template"
    )
    db.refresh(day)
    return day


@router.put("/program-template-days/{day_id}", response_model=ProgramTemplateDaySchema)
def update_template_day(
    day_id: int,
    data: ProgramTemplateDayUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    changes = data.model_dump(exclude_unset=True)
    new_number = changes.get("day_number", day.day_number)
    new_order = changes.get("order_index", day.order_index)
    duplicate = db.query(ProgramTemplateDay.id).filter(
        ProgramTemplateDay.template_id == day.template_id,
        ProgramTemplateDay.id != day.id,
        (
            (ProgramTemplateDay.day_number == new_number)
            | (ProgramTemplateDay.order_index == new_order)
        ),
    ).first()
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Day number and order must be unique within a template")
    for field, value in changes.items():
        setattr(day, field, value)
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.day_updated",
        target_type="program_template_day",
        target_id=day.id,
        metadata={"field_count": len(changes)},
    )
    _commit_admin_content(
        db, "Day number and order must be unique within a template"
    )
    db.refresh(day)
    return day


@router.delete("/program-template-days/{day_id}")
def delete_template_day(
    day_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.day_deleted",
        target_type="program_template_day",
        target_id=day.id,
        metadata={"template_id": day.template_id},
    )
    db.delete(day)
    _commit_admin_content(db, "Template day is still referenced and cannot be deleted")
    return {"message": "Day deleted"}


# Template Exercises

@router.post("/program-template-days/{day_id}/exercises", response_model=ProgramTemplateExerciseSchema, status_code=status.HTTP_201_CREATED)
def add_template_exercise(
    day_id: int,
    data: ProgramTemplateExerciseCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    duplicate = db.query(ProgramTemplateExercise.id).filter(
        ProgramTemplateExercise.day_id == day_id,
        ProgramTemplateExercise.order_index == data.order_index,
    ).first()
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Exercise order must be unique within a day")
    exercise = ProgramTemplateExercise(day_id=day_id, **data.model_dump())
    db.add(exercise)
    _flush_admin_content(db, "Exercise order must be unique within a day")
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.exercise_created",
        target_type="program_template_exercise",
        target_id=exercise.id,
        metadata={"day_id": day_id},
    )
    _commit_admin_content(db, "Exercise order must be unique within a day")
    db.refresh(exercise)
    return exercise


@router.put("/program-template-exercises/{exercise_id}", response_model=ProgramTemplateExerciseSchema)
def update_template_exercise(
    exercise_id: int,
    data: ProgramTemplateExerciseUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    exercise = db.query(ProgramTemplateExercise).filter(ProgramTemplateExercise.id == exercise_id).first()
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    changes = data.model_dump(exclude_unset=True)
    new_order = changes.get("order_index", exercise.order_index)
    duplicate = db.query(ProgramTemplateExercise.id).filter(
        ProgramTemplateExercise.day_id == exercise.day_id,
        ProgramTemplateExercise.id != exercise.id,
        ProgramTemplateExercise.order_index == new_order,
    ).first()
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "Exercise order must be unique within a day")
    for field, value in changes.items():
        setattr(exercise, field, value)
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.exercise_updated",
        target_type="program_template_exercise",
        target_id=exercise.id,
        metadata={"field_count": len(changes)},
    )
    _commit_admin_content(db, "Exercise order must be unique within a day")
    db.refresh(exercise)
    return exercise


@router.delete("/program-template-exercises/{exercise_id}")
def delete_template_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    exercise = db.query(ProgramTemplateExercise).filter(ProgramTemplateExercise.id == exercise_id).first()
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    add_admin_audit_event(
        db,
        admin=admin,
        action="program_template.exercise_deleted",
        target_type="program_template_exercise",
        target_id=exercise.id,
        metadata={"day_id": exercise.day_id},
    )
    db.delete(exercise)
    _commit_admin_content(db, "Template exercise could not be deleted")
    return {"message": "Exercise deleted"}


# ═══════════════════════════════════════════════════════════
# FOOD CATEGORIES
# ═══════════════════════════════════════════════════════════

@router.get("/food-categories", response_model=PaginatedResponse)
def list_food_categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(FoodCategory)
    if search:
        query = query.filter(FoodCategory.name.ilike(f"%{search.strip()}%"))
    total = query.count()
    rows = (
        query.order_by(FoodCategory.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [FoodCategoryResponse.model_validate(row).model_dump() for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/food-categories", response_model=FoodCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_food_category(data: FoodCategoryCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    category = FoodCategory(**data.model_dump())
    db.add(category)
    _flush_admin_content(db, "A food category with that name already exists")
    add_admin_audit_event(db, admin=admin, action="food_category.created", target_type="food_category", target_id=category.id)
    _commit_admin_content(db, "A food category with that name already exists")
    db.refresh(category)
    return category


@router.get("/food-categories/{category_id}", response_model=FoodCategoryResponse)
def get_food_category(category_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.put("/food-categories/{category_id}", response_model=FoodCategoryResponse)
def update_food_category(category_id: int, data: FoodCategoryUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(category, field, value)
    add_admin_audit_event(db, admin=admin, action="food_category.updated", target_type="food_category", target_id=category.id, metadata={"field_count": len(changes)})
    _commit_admin_content(db, "A food category with that name already exists")
    db.refresh(category)
    return category


@router.delete("/food-categories/{category_id}")
def delete_food_category(category_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    add_admin_audit_event(db, admin=admin, action="food_category.deleted", target_type="food_category", target_id=category.id)
    db.delete(category)
    _commit_admin_content(db, "Food category is still referenced and cannot be deleted")
    return {"message": "Category deleted"}


# ═══════════════════════════════════════════════════════════
# FOODS
# ═══════════════════════════════════════════════════════════

@router.get("/foods", response_model=PaginatedResponse)
def list_foods(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    category_id: Optional[int] = Query(None, ge=1),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(Food)
    if search:
        pattern = f"%{search}%"
        query = query.filter((Food.name.ilike(pattern)) | (Food.brand.ilike(pattern)))
    if category_id is not None:
        query = query.filter(Food.category_id == category_id)
    if is_active is not None:
        query = query.filter(Food.is_active.is_(is_active))
    total = query.count()
    rows = (
        query.order_by(Food.name.asc(), Food.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [FoodResponse.model_validate(row).model_dump() for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/foods", response_model=FoodResponse, status_code=status.HTTP_201_CREATED)
def create_food(data: FoodCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    if data.category_id is not None and db.query(FoodCategory.id).filter(FoodCategory.id == data.category_id).first() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Food category does not exist")
    food = Food(**data.model_dump())
    db.add(food)
    _flush_admin_content(db, "Food conflicts with existing catalog data")
    add_admin_audit_event(db, admin=admin, action="food.created", target_type="food", target_id=food.id)
    _commit_admin_content(db, "Food conflicts with existing catalog data")
    db.refresh(food)
    return food


@router.get("/foods/{food_id}", response_model=FoodResponse)
def get_food(food_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    return food


@router.put("/foods/{food_id}", response_model=FoodResponse)
def update_food(food_id: int, data: FoodUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    changes = data.model_dump(exclude_unset=True)
    if (
        "category_id" in changes
        and changes["category_id"] is not None
        and db.query(FoodCategory.id)
        .filter(FoodCategory.id == changes["category_id"])
        .first()
        is None
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Food category does not exist")
    for field, value in changes.items():
        setattr(food, field, value)
    add_admin_audit_event(db, admin=admin, action="food.updated", target_type="food", target_id=food.id, metadata={"field_count": len(changes)})
    _commit_admin_content(db, "Food conflicts with existing catalog data")
    db.refresh(food)
    return food


@router.delete("/foods/{food_id}")
def delete_food(food_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    add_admin_audit_event(db, admin=admin, action="food.deleted", target_type="food", target_id=food.id)
    db.delete(food)
    _commit_admin_content(db, "Food is still referenced and cannot be deleted")
    return {"message": "Food deleted"}


# ═══════════════════════════════════════════════════════════
# MICRONUTRIENTS
# ═══════════════════════════════════════════════════════════

@router.get("/micronutrients", response_model=PaginatedResponse)
def list_micronutrients(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    category: Optional[str] = Query(None, max_length=50),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(Micronutrient)
    if search:
        query = query.filter(Micronutrient.name.ilike(f"%{search.strip()}%"))
    if category:
        query = query.filter(Micronutrient.category == category)
    total = query.count()
    rows = (
        query.order_by(Micronutrient.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return page_payload(
        [MicronutrientResponse.model_validate(row).model_dump() for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/micronutrients", response_model=MicronutrientResponse, status_code=status.HTTP_201_CREATED)
def create_micronutrient(data: MicronutrientCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    mn = Micronutrient(**data.model_dump())
    db.add(mn)
    _flush_admin_content(db, "A micronutrient with that name already exists")
    add_admin_audit_event(db, admin=admin, action="micronutrient.created", target_type="micronutrient", target_id=mn.id)
    _commit_admin_content(db, "A micronutrient with that name already exists")
    db.refresh(mn)
    return mn


@router.get("/micronutrients/{mn_id}", response_model=MicronutrientResponse)
def get_micronutrient(mn_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    return mn


@router.put("/micronutrients/{mn_id}", response_model=MicronutrientResponse)
def update_micronutrient(mn_id: int, data: MicronutrientUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(mn, field, value)
    add_admin_audit_event(db, admin=admin, action="micronutrient.updated", target_type="micronutrient", target_id=mn.id, metadata={"field_count": len(changes)})
    _commit_admin_content(db, "A micronutrient with that name already exists")
    db.refresh(mn)
    return mn


@router.delete("/micronutrients/{mn_id}")
def delete_micronutrient(mn_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    add_admin_audit_event(db, admin=admin, action="micronutrient.deleted", target_type="micronutrient", target_id=mn.id)
    db.delete(mn)
    _commit_admin_content(db, "Micronutrient is still referenced and cannot be deleted")
    return {"message": "Micronutrient deleted"}


# ═══════════════════════════════════════════════════════════
# FOOD MICRONUTRIENTS
# ═══════════════════════════════════════════════════════════

@router.get("/foods/{food_id}/micronutrients", response_model=PaginatedResponse)
def list_food_micronutrients(
    food_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    query = db.query(FoodMicronutrient).filter(FoodMicronutrient.food_id == food_id)
    total = query.count()
    rows = query.order_by(FoodMicronutrient.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return page_payload(
        [FoodMicronutrientResponse.model_validate(row).model_dump() for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/foods/{food_id}/micronutrients", response_model=FoodMicronutrientResponse, status_code=status.HTTP_201_CREATED)
def add_food_micronutrient(food_id: int, data: FoodMicronutrientCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    if db.query(Micronutrient.id).filter(Micronutrient.id == data.micronutrient_id).first() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Micronutrient does not exist")
    if db.query(FoodMicronutrient.id).filter(
        FoodMicronutrient.food_id == food_id,
        FoodMicronutrient.micronutrient_id == data.micronutrient_id,
    ).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This food already has that micronutrient link")
    mn = FoodMicronutrient(food_id=food_id, **data.model_dump())
    db.add(mn)
    _flush_admin_content(db, "This food already has that micronutrient link")
    add_admin_audit_event(db, admin=admin, action="food_micronutrient.created", target_type="food_micronutrient", target_id=mn.id, metadata={"food_id": food_id})
    _commit_admin_content(db, "This food already has that micronutrient link")
    db.refresh(mn)
    return mn


@router.put("/food-micronutrients/{id}", response_model=FoodMicronutrientResponse)
def update_food_micronutrient(id: int, data: FoodMicronutrientUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    mn = db.query(FoodMicronutrient).filter(FoodMicronutrient.id == id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(mn, field, value)
    add_admin_audit_event(db, admin=admin, action="food_micronutrient.updated", target_type="food_micronutrient", target_id=mn.id, metadata={"field_count": len(changes)})
    _commit_admin_content(db, "This food already has that micronutrient link")
    db.refresh(mn)
    return mn


@router.delete("/food-micronutrients/{id}")
def delete_food_micronutrient(id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    mn = db.query(FoodMicronutrient).filter(FoodMicronutrient.id == id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    add_admin_audit_event(db, admin=admin, action="food_micronutrient.deleted", target_type="food_micronutrient", target_id=mn.id, metadata={"food_id": mn.food_id})
    db.delete(mn)
    _commit_admin_content(db, "Micronutrient link could not be deleted")
    return {"message": "Record deleted"}


# ═══════════════════════════════════════════════════════════
# OPERATIONS / AUDIT
# ═══════════════════════════════════════════════════════════

@router.get("/audit-events", response_model=AdminAuditPage)
def list_admin_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    admin_user_id: Optional[int] = Query(None, ge=1),
    action: Optional[str] = Query(None, max_length=100),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(AdminAuditEvent)
    if admin_user_id is not None:
        query = query.filter(AdminAuditEvent.admin_user_id == admin_user_id)
    if action:
        query = query.filter(AdminAuditEvent.action == action)
    if start_date:
        query = query.filter(
            AdminAuditEvent.created_at
            >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        )
    if end_date:
        query = query.filter(
            AdminAuditEvent.created_at
            < datetime.combine(
                end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            )
        )
    total = query.count()
    rows = (
        query.order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    admin_ids = {row.admin_user_id for row in rows if row.admin_user_id is not None}
    admin_emails = (
        {
            user.id: user.email
            for user in db.query(User).filter(User.id.in_(admin_ids)).all()
        }
        if admin_ids
        else {}
    )
    return {
        "items": [
            {
                "id": row.id,
                "admin_user_id": row.admin_user_id,
                "admin_email": admin_emails.get(row.admin_user_id),
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "metadata": row.metadata_json or {},
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/operations/health", response_model=SystemHealthResponse)
def get_system_health(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return system_health(db)


@router.get("/security/status")
def get_admin_security_status(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    superadmin_count = db.query(User.id).filter(User.role == "superadmin").count()
    return {
        "current_role": admin.role,
        "superadmin_configured": superadmin_count > 0,
        "mfa_configured": False,
        "mfa_follow_up_required": True,
        "legacy_critical_access": superadmin_count == 0,
    }
