from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from auth.utils import get_current_admin
from database import get_db
from models.user import User
from models.admin import ProgramTemplate, ProgramTemplateDay, ProgramTemplateExercise
from models.food import FoodCategory, Food, Micronutrient, FoodMicronutrient
from models.program import Program, ProgramDay, ProgramExercise
from models.workout import Workout
from models.nutrition import NutritionLog
from models.token import RevokedToken

from schemas.admin import DashboardResponse, AdminUserResponse, UpdateRoleBody
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

router = APIRouter()


# ═══════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════

@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Return admin dashboard statistics."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    total_users = db.query(User).count()
    admin_users = db.query(User).filter(User.role == "admin").count()
    normal_users = db.query(User).filter(User.role == "user").count()
    verified_users = db.query(User).filter(User.is_verified == True).count()
    unverified_users = db.query(User).filter(User.is_verified == False).count()

    recently_active_users = (
        db.query(User)
        .filter(User.last_login_at >= cutoff_24h)
        .count()
    )
    logged_out_users = (
        db.query(User)
        .filter(
            User.last_logout_at != None,
            User.last_logout_at > User.last_login_at,
        )
        .count()
    )

    total_program_templates = db.query(ProgramTemplate).count()
    total_foods = db.query(Food).count()
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

@router.get("/users", response_model=list[AdminUserResponse])
def list_users(
    search: Optional[str] = Query(None, description="Search by email or full_name"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Return all users, optionally filtered by email or full_name."""
    query = db.query(User)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            (User.email.ilike(pattern)) | (User.full_name.ilike(pattern))
        )
    return query.order_by(User.id.asc()).all()


@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Return a single user by ID."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.put("/users/{user_id}/role", response_model=AdminUserResponse)
def update_user_role(
    user_id: int,
    body: UpdateRoleBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Promote or demote a user's role. Prevents removing the last admin."""
    if body.role not in ("user", "admin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be 'user' or 'admin'",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Prevent last-admin demotion
    if user.role == "admin" and body.role == "user":
        if admin.id == user.id:
            admin_count = db.query(User).filter(User.role == "admin").count()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote yourself — you are the only admin",
                )

    user.role = body.role
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
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

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 1. Revoked tokens
    db.query(RevokedToken).filter(RevokedToken.user_id == user_id).delete(synchronize_session=False)

    # 2. Program exercises → program days → programs
    program_ids = [p.id for p in db.query(Program.id).filter(Program.user_id == user_id).all()]
    if program_ids:
        day_ids = [d.id for d in db.query(ProgramDay.id).filter(ProgramDay.program_id.in_(program_ids)).all()]
        if day_ids:
            db.query(ProgramExercise).filter(
                ProgramExercise.program_day_id.in_(day_ids)
            ).delete(synchronize_session=False)
        db.query(ProgramDay).filter(ProgramDay.program_id.in_(program_ids)).delete(synchronize_session=False)
        db.query(Program).filter(Program.user_id == user_id).delete(synchronize_session=False)

    # 3. Workouts (workout_sets should cascade from workout)
    db.query(Workout).filter(Workout.user_id == user_id).delete(synchronize_session=False)

    # 4. Nutrition logs
    db.query(NutritionLog).filter(NutritionLog.user_id == user_id).delete(synchronize_session=False)

    # 5. Delete user
    db.delete(user)
    db.commit()

    return {"message": f"User {user_id} deleted successfully"}


# ═══════════════════════════════════════════════════════════
# PROGRAM TEMPLATES
# ═══════════════════════════════════════════════════════════

@router.get("/program-templates", response_model=list[ProgramTemplateSummary])
def list_program_templates(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    return db.query(ProgramTemplate).order_by(ProgramTemplate.id.asc()).all()


@router.post("/program-templates", response_model=ProgramTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_program_template(
    data: ProgramTemplateCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    template = ProgramTemplate(**data.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/program-templates/{template_id}", response_model=ProgramTemplateResponse)
def get_program_template(
    template_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
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
    _admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(template, field, value)
    db.commit()
    db.refresh(template)
    return template


@router.delete("/program-templates/{template_id}")
def delete_program_template(
    template_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    db.delete(template)
    db.commit()
    return {"message": "Template deleted"}


# Template Days

@router.post("/program-templates/{template_id}/days", response_model=ProgramTemplateDaySchema, status_code=status.HTTP_201_CREATED)
def add_template_day(
    template_id: int,
    data: ProgramTemplateDayCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    template = db.query(ProgramTemplate).filter(ProgramTemplate.id == template_id).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    day = ProgramTemplateDay(template_id=template_id, **data.model_dump())
    db.add(day)
    db.commit()
    db.refresh(day)
    return day


@router.put("/program-template-days/{day_id}", response_model=ProgramTemplateDaySchema)
def update_template_day(
    day_id: int,
    data: ProgramTemplateDayUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(day, field, value)
    db.commit()
    db.refresh(day)
    return day


@router.delete("/program-template-days/{day_id}")
def delete_template_day(
    day_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    db.delete(day)
    db.commit()
    return {"message": "Day deleted"}


# Template Exercises

@router.post("/program-template-days/{day_id}/exercises", response_model=ProgramTemplateExerciseSchema, status_code=status.HTTP_201_CREATED)
def add_template_exercise(
    day_id: int,
    data: ProgramTemplateExerciseCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    day = db.query(ProgramTemplateDay).filter(ProgramTemplateDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    exercise = ProgramTemplateExercise(day_id=day_id, **data.model_dump())
    db.add(exercise)
    db.commit()
    db.refresh(exercise)
    return exercise


@router.put("/program-template-exercises/{exercise_id}", response_model=ProgramTemplateExerciseSchema)
def update_template_exercise(
    exercise_id: int,
    data: ProgramTemplateExerciseUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    exercise = db.query(ProgramTemplateExercise).filter(ProgramTemplateExercise.id == exercise_id).first()
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(exercise, field, value)
    db.commit()
    db.refresh(exercise)
    return exercise


@router.delete("/program-template-exercises/{exercise_id}")
def delete_template_exercise(
    exercise_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    exercise = db.query(ProgramTemplateExercise).filter(ProgramTemplateExercise.id == exercise_id).first()
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    db.delete(exercise)
    db.commit()
    return {"message": "Exercise deleted"}


# ═══════════════════════════════════════════════════════════
# FOOD CATEGORIES
# ═══════════════════════════════════════════════════════════

@router.get("/food-categories", response_model=list[FoodCategoryResponse])
def list_food_categories(db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    return db.query(FoodCategory).order_by(FoodCategory.name.asc()).all()


@router.post("/food-categories", response_model=FoodCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_food_category(data: FoodCategoryCreate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    category = FoodCategory(**data.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.get("/food-categories/{category_id}", response_model=FoodCategoryResponse)
def get_food_category(category_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.put("/food-categories/{category_id}", response_model=FoodCategoryResponse)
def update_food_category(category_id: int, data: FoodCategoryUpdate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/food-categories/{category_id}")
def delete_food_category(category_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    category = db.query(FoodCategory).filter(FoodCategory.id == category_id).first()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    db.delete(category)
    db.commit()
    return {"message": "Category deleted"}


# ═══════════════════════════════════════════════════════════
# FOODS
# ═══════════════════════════════════════════════════════════

@router.get("/foods", response_model=list[FoodResponse])
def list_foods(
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    query = db.query(Food)
    if search:
        pattern = f"%{search}%"
        query = query.filter((Food.name.ilike(pattern)) | (Food.brand.ilike(pattern)))
    return query.order_by(Food.name.asc()).all()


@router.post("/foods", response_model=FoodResponse, status_code=status.HTTP_201_CREATED)
def create_food(data: FoodCreate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = Food(**data.model_dump())
    db.add(food)
    db.commit()
    db.refresh(food)
    return food


@router.get("/foods/{food_id}", response_model=FoodResponse)
def get_food(food_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    return food


@router.put("/foods/{food_id}", response_model=FoodResponse)
def update_food(food_id: int, data: FoodUpdate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(food, field, value)
    db.commit()
    db.refresh(food)
    return food


@router.delete("/foods/{food_id}")
def delete_food(food_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    db.delete(food)
    db.commit()
    return {"message": "Food deleted"}


# ═══════════════════════════════════════════════════════════
# MICRONUTRIENTS
# ═══════════════════════════════════════════════════════════

@router.get("/micronutrients", response_model=list[MicronutrientResponse])
def list_micronutrients(db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    return db.query(Micronutrient).order_by(Micronutrient.name.asc()).all()


@router.post("/micronutrients", response_model=MicronutrientResponse, status_code=status.HTTP_201_CREATED)
def create_micronutrient(data: MicronutrientCreate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = Micronutrient(**data.model_dump())
    db.add(mn)
    db.commit()
    db.refresh(mn)
    return mn


@router.get("/micronutrients/{mn_id}", response_model=MicronutrientResponse)
def get_micronutrient(mn_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    return mn


@router.put("/micronutrients/{mn_id}", response_model=MicronutrientResponse)
def update_micronutrient(mn_id: int, data: MicronutrientUpdate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(mn, field, value)
    db.commit()
    db.refresh(mn)
    return mn


@router.delete("/micronutrients/{mn_id}")
def delete_micronutrient(mn_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(Micronutrient).filter(Micronutrient.id == mn_id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Micronutrient not found")
    db.delete(mn)
    db.commit()
    return {"message": "Micronutrient deleted"}


# ═══════════════════════════════════════════════════════════
# FOOD MICRONUTRIENTS
# ═══════════════════════════════════════════════════════════

@router.get("/foods/{food_id}/micronutrients", response_model=list[FoodMicronutrientResponse])
def list_food_micronutrients(food_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    return db.query(FoodMicronutrient).filter(FoodMicronutrient.food_id == food_id).all()


@router.post("/foods/{food_id}/micronutrients", response_model=FoodMicronutrientResponse, status_code=status.HTTP_201_CREATED)
def add_food_micronutrient(food_id: int, data: FoodMicronutrientCreate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    food = db.query(Food).filter(Food.id == food_id).first()
    if food is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food not found")
    mn = FoodMicronutrient(food_id=food_id, **data.model_dump())
    db.add(mn)
    db.commit()
    db.refresh(mn)
    return mn


@router.put("/food-micronutrients/{id}", response_model=FoodMicronutrientResponse)
def update_food_micronutrient(id: int, data: FoodMicronutrientUpdate, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(FoodMicronutrient).filter(FoodMicronutrient.id == id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(mn, field, value)
    db.commit()
    db.refresh(mn)
    return mn


@router.delete("/food-micronutrients/{id}")
def delete_food_micronutrient(id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin)):
    mn = db.query(FoodMicronutrient).filter(FoodMicronutrient.id == id).first()
    if mn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    db.delete(mn)
    db.commit()
    return {"message": "Record deleted"}
