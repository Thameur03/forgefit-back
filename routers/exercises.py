import os
import time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Query, HTTPException, Path, status
from sqlalchemy.orm import Session
from sqlalchemy import func
import httpx

from database import get_db
from models.user import User
from models.workout import Workout, WorkoutSet
from auth.utils import get_current_user

router = APIRouter()

EXERCISEDB_URL = os.getenv("EXERCISEDB_URL", "https://exercisedb-apiii.vercel.app")

_cache: dict = {}
_cache_time: dict = {}
CACHE_TTL = 3600  # 1 hour

def _get_from_cache(key: str) -> Optional[Any]:
    now = time.time()
    if key in _cache and key in _cache_time:
        if now - _cache_time[key] < CACHE_TTL:
            return _cache[key]
        else:
            del _cache[key]
            del _cache_time[key]
    return None

def _set_in_cache(key: str, value: Any):
    _cache[key] = value
    _cache_time[key] = time.time()

def _normalize_exercise(data: dict) -> dict:
    return {
        "id": data.get("exerciseId", ""),
        "name": data.get("name", ""),
        "gif_url": data.get("gifUrl", ""),
        "target_muscles": data.get("targetMuscles", []),
        "body_parts": data.get("bodyParts", []),
        "equipment": data.get("equipments", []),
        "secondary_muscles": data.get("secondaryMuscles", []),
        "instructions": data.get("instructions", [])
    }

@router.get("/search")
def search_exercises(
    q: str = Query(..., description="Search term"),
    current_user: User = Depends(get_current_user),
):
    cache_key = f"search_{q}"
    cached_result = _get_from_cache(cache_key)
    if cached_result is not None:
        return cached_result
    
    try:
        response = httpx.get(
            f"{EXERCISEDB_URL}/api/v1/exercises/search",
            params={"q": q, "limit": 10},
            timeout=10.0
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success"):
            exercises = data.get("data", [])
            normalized = [_normalize_exercise(ex) for ex in exercises]
            _set_in_cache(cache_key, normalized)
            return normalized
        return []
        
    except Exception:
        return []

@router.get("/recent")
def get_recent_exercises(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the 8 most recently used exercise names for the current user."""
    results = (
        db.query(WorkoutSet.exercise_name)
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(Workout.user_id == current_user.id)
        .group_by(WorkoutSet.exercise_name)
        .order_by(func.max(Workout.date).desc())
        .limit(8)
        .all()
    )

    exercises = [row[0] for row in results]
    return {"exercises": exercises}

@router.get("/{exercise_id}")
def get_exercise_by_id(
    exercise_id: str = Path(..., description="Exercise ID"),
    current_user: User = Depends(get_current_user),
):
    cache_key = f"exercise_{exercise_id}"
    cached_result = _get_from_cache(cache_key)
    if cached_result is not None:
        return cached_result
    
    try:
        response = httpx.get(
            f"{EXERCISEDB_URL}/api/v1/exercises/{exercise_id}",
            timeout=10.0
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success") and "data" in data:
            normalized = _normalize_exercise(data["data"])
            _set_in_cache(cache_key, normalized)
            return normalized
        raise HTTPException(status_code=404, detail="Exercise not found")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Exercise not found")
        raise HTTPException(status_code=503, detail="Exercise details unavailable")
    except Exception:
        raise HTTPException(status_code=503, detail="Exercise details unavailable")

@router.get("/{exercise_name}/history")
def get_exercise_history(
    exercise_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the last 10 sessions where the current user logged a specific exercise."""
    rows = (
        db.query(
            Workout.date,
            WorkoutSet.sets,
            WorkoutSet.reps,
            WorkoutSet.weight_kg,
            Workout.id.label("workout_id"),
        )
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .filter(
            Workout.user_id == current_user.id,
            func.lower(WorkoutSet.exercise_name) == exercise_name.lower(),
        )
        .order_by(Workout.date.desc(), WorkoutSet.id.desc())
        .limit(10)
        .all()
    )

    history = [
        {
            "date": str(row.date),
            "sets": row.sets,
            "reps": row.reps,
            "weight_kg": row.weight_kg,
            "workout_id": row.workout_id,
        }
        for row in rows
    ]

    return {"exercise_name": exercise_name, "history": history}