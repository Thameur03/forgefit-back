"""Admin CRUD endpoints for food search filters.

All endpoints require admin authentication via ``get_current_admin``.
DELETE performs a **soft delete** (sets ``is_active = False``).
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.utils import get_current_admin
from database import get_db
from models.food_filter import FoodFilter
from models.user import User
from services.admin_operations import add_admin_audit_event
from schemas.food_filter import (
    FoodFilterCreate,
    FoodFilterResponse,
    FoodFilterUpdate,
    slugify,
)

router = APIRouter()


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A food filter with that slug already exists",
        ) from exc


@router.get("/food-filters", response_model=List[FoodFilterResponse])
def list_food_filters(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Return **all** food filters (including inactive), ordered by
    ``sort_order`` then ``name``."""
    return (
        db.query(FoodFilter)
        .order_by(FoodFilter.sort_order.asc(), FoodFilter.name.asc())
        .all()
    )


@router.post(
    "/food-filters",
    response_model=FoodFilterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_food_filter(
    payload: FoodFilterCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    slug = payload.slug or slugify(payload.name)

    if db.query(FoodFilter).filter(FoodFilter.slug == slug).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A food filter with slug '{slug}' already exists",
        )

    food_filter = FoodFilter(
        name=payload.name,
        slug=slug,
        description=payload.description,
        default_query=payload.default_query,
        include_keywords=payload.include_keywords,
        exclude_keywords=payload.exclude_keywords,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(food_filter)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A food filter with that slug already exists",
        ) from exc
    add_admin_audit_event(
        db,
        admin=admin,
        action="food_filter.created",
        target_type="food_filter",
        target_id=food_filter.id,
    )
    _commit_or_conflict(db)
    db.refresh(food_filter)
    return food_filter


@router.get("/food-filters/{filter_id}", response_model=FoodFilterResponse)
def get_food_filter(
    filter_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    food_filter = db.query(FoodFilter).filter(FoodFilter.id == filter_id).first()
    if not food_filter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Food filter not found",
        )
    return food_filter


@router.put("/food-filters/{filter_id}", response_model=FoodFilterResponse)
def update_food_filter(
    filter_id: int,
    payload: FoodFilterUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    food_filter = db.query(FoodFilter).filter(FoodFilter.id == filter_id).first()
    if not food_filter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Food filter not found",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # Validate slug uniqueness if changing
    if "slug" in update_data and update_data["slug"] is not None:
        existing = (
            db.query(FoodFilter)
            .filter(FoodFilter.slug == update_data["slug"], FoodFilter.id != filter_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A food filter with slug '{update_data['slug']}' already exists",
            )

    # Clean keywords if provided
    if "include_keywords" in update_data and update_data["include_keywords"] is not None:
        update_data["include_keywords"] = [
            kw.strip().lower()
            for kw in update_data["include_keywords"]
            if kw and kw.strip()
        ]
    if "exclude_keywords" in update_data and update_data["exclude_keywords"] is not None:
        update_data["exclude_keywords"] = [
            kw.strip().lower()
            for kw in update_data["exclude_keywords"]
            if kw and kw.strip()
        ]

    for key, value in update_data.items():
        setattr(food_filter, key, value)

    add_admin_audit_event(
        db,
        admin=admin,
        action="food_filter.updated",
        target_type="food_filter",
        target_id=food_filter.id,
        metadata={"field_count": len(update_data)},
    )

    _commit_or_conflict(db)
    db.refresh(food_filter)
    return food_filter


@router.delete("/food-filters/{filter_id}")
def deactivate_food_filter(
    filter_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Soft-delete: sets ``is_active = False`` instead of removing the row."""
    food_filter = db.query(FoodFilter).filter(FoodFilter.id == filter_id).first()
    if not food_filter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Food filter not found",
        )
    food_filter.is_active = False
    add_admin_audit_event(
        db,
        admin=admin,
        action="food_filter.deactivated",
        target_type="food_filter",
        target_id=food_filter.id,
    )
    _commit_or_conflict(db)
    return {"message": "Food filter deactivated"}
