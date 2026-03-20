from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from database import get_db
from models.user import User
from models.workout import Workout, WorkoutSet
from schemas.workout import (
    WorkoutCreate,
    WorkoutUpdate,
    WorkoutResponse,
    WorkoutSummary,
    WorkoutSetCreate,
    WorkoutSetResponse,
    LastSessionData,
)
from auth.utils import get_current_user

router = APIRouter()


def _get_last_session(
    db: Session, user_id: int, exercise_name: str, before_date: date
) -> Optional[LastSessionData]:
    """
    Fetch the most recent previous session for a given exercise.
    Returns LastSessionData or None.
    """
    result = (
        db.query(WorkoutSet, Workout)
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(
            Workout.user_id == user_id,
            func.lower(WorkoutSet.exercise_name) == exercise_name.lower(),
            Workout.date < before_date,
        )
        .order_by(Workout.date.desc(), WorkoutSet.id.desc())
        .first()
    )
    if result is None:
        return None
    workout_set, workout = result
    return LastSessionData(
        date=workout.date,
        sets=workout_set.sets,
        reps=workout_set.reps,
        weight_kg=workout_set.weight_kg,
    )


def _compute_totals(workout_sets: list) -> tuple:
    """Compute total_sets count and total_volume_kg for a list of WorkoutSet objects."""
    total_sets = len(workout_sets)
    total_volume_kg = sum(
        s.sets * s.reps * (s.weight_kg or 0)
        for s in workout_sets
    )
    return total_sets, total_volume_kg


def _build_workout_response(db: Session, workout: Workout, user: User) -> dict:
    """Build a WorkoutResponse dict with sets and computed totals."""
    sets_response = []
    # TODO: batch last-session lookups to avoid N+1 queries
    for s in workout.sets:
        last_session = _get_last_session(db, workout.user_id, s.exercise_name, workout.date)
        sets_response.append(
            WorkoutSetResponse(
                id=s.id,
                exercise_name=s.exercise_name,
                sets=s.sets,
                reps=s.reps,
                weight_kg=s.weight_kg,
                last_session=last_session,
            )
        )
    total_sets, total_volume_kg = _compute_totals(workout.sets)
    
    # Calculate calories burned
    body_weight = user.weight_kg or 75.0
    MET_WEIGHT_TRAINING = 3.5
    duration_minutes = (workout.duration_seconds or 0) / 60
    calories = int((MET_WEIGHT_TRAINING * body_weight * duration_minutes) / 60)

    return {
        "id": workout.id,
        "user_id": workout.user_id,
        "date": workout.date,
        "notes": workout.notes,
        "name": workout.name,
        "duration_seconds": workout.duration_seconds,
        "calories_burned": calories,
        "sets": sets_response,
        "total_sets": total_sets,
        "total_volume_kg": total_volume_kg,
    }


@router.post("/", response_model=WorkoutResponse, status_code=status.HTTP_201_CREATED)
def create_workout(
    data: WorkoutCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new workout session. Defaults to today's date if not specified."""
    workout_date = data.date if data.date is not None else date.today()
    workout = Workout(
        user_id=current_user.id,
        date=workout_date,
        notes=data.notes,
        name=data.name,
        duration_seconds=data.duration_seconds,
    )
    db.add(workout)
    db.commit()
    db.refresh(workout)
    return _build_workout_response(db, workout, current_user)


@router.post(
    "/{workout_id}/sets",
    response_model=WorkoutSetResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_set(
    workout_id: int,
    data: WorkoutSetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a set to an existing workout. Returns the set with last session data."""
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found"
        )
    if workout.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this workout",
        )
    workout_set = WorkoutSet(
        workout_id=workout_id,
        exercise_name=data.exercise_name,
        sets=data.sets,
        reps=data.reps,
        weight_kg=data.weight_kg,
    )
    db.add(workout_set)
    db.commit()
    db.refresh(workout_set)
    last_session = _get_last_session(
        db, current_user.id, data.exercise_name, workout.date
    )
    return WorkoutSetResponse(
        id=workout_set.id,
        exercise_name=workout_set.exercise_name,
        sets=workout_set.sets,
        reps=workout_set.reps,
        weight_kg=workout_set.weight_kg,
        last_session=last_session,
    )


@router.get("/", response_model=list[WorkoutSummary], status_code=status.HTTP_200_OK)
def list_workouts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List workout summaries for the current user, ordered by date descending."""
    workouts = (
        db.query(Workout)
        .options(joinedload(Workout.sets))
        .filter(Workout.user_id == current_user.id)
        .order_by(Workout.date.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    summaries = []
    
    body_weight = current_user.weight_kg or 75.0
    MET_WEIGHT_TRAINING = 3.5
    
    for w in workouts:
        total_sets, total_volume_kg = _compute_totals(w.sets)
        
        duration_minutes = (w.duration_seconds or 0) / 60
        calories = int((MET_WEIGHT_TRAINING * body_weight * duration_minutes) / 60)
        
        summaries.append(
            {
                "id": w.id,
                "user_id": w.user_id,
                "date": w.date,
                "notes": w.notes,
                "name": w.name,
                "duration_seconds": w.duration_seconds,
                "calories_burned": calories,
                "total_sets": total_sets,
                "total_volume_kg": total_volume_kg,
            }
        )
    return summaries


@router.get(
    "/{workout_id}", response_model=WorkoutResponse, status_code=status.HTTP_200_OK
)
def get_workout(
    workout_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full workout details with all sets and last session data."""
    workout = (
        db.query(Workout)
        .options(joinedload(Workout.sets))
        .filter(Workout.id == workout_id)
        .first()
    )
    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found"
        )
    if workout.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this workout",
        )
    return _build_workout_response(db, workout, current_user)


@router.put(
    "/{workout_id}", response_model=WorkoutResponse, status_code=status.HTTP_200_OK
)
def update_workout(
    workout_id: int,
    data: WorkoutUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update workout date, notes, name, or duration_seconds."""
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found"
        )
    if workout.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this workout",
        )
    if data.name is not None:
        workout.name = data.name
    if data.notes is not None:
        workout.notes = data.notes
    if data.date is not None:
        workout.date = data.date
    if data.duration_seconds is not None:
        workout.duration_seconds = data.duration_seconds
    if data.calories_burned is not None:
        workout.calories_burned = data.calories_burned
        
    db.commit()
    db.refresh(workout)
    return _build_workout_response(db, workout, current_user)


@router.delete("/{workout_id}", status_code=status.HTTP_200_OK)
def delete_workout(
    workout_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a workout and all its sets (cascade)."""
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found"
        )
    if workout.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this workout",
        )
    db.delete(workout)
    db.commit()
    return {"message": "Workout deleted"}


@router.delete("/{workout_id}/sets/{set_id}", status_code=status.HTTP_200_OK)
def delete_set(
    workout_id: int,
    set_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single set from a workout."""
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if workout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found"
        )
    if workout.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete from this workout",
        )
    
    workout_set = db.query(WorkoutSet).filter(WorkoutSet.id == set_id, WorkoutSet.workout_id == workout_id).first()
    if workout_set is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Set not found"
        )
    
    db.delete(workout_set)
    db.commit()
    return {"message": "Set deleted"}
