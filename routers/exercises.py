from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func
import httpx
from cachetools import TTLCache

from database import get_db
from models.user import User
from models.workout import Workout, WorkoutSet
from auth.utils import get_current_user

router = APIRouter()

# ---------------------------------------------------------------------------
# Muscle-name lookup (populated once from wger API, with hardcoded fallback)
# ---------------------------------------------------------------------------
_MUSCLE_FALLBACK: dict[int, str] = {
    1: "Biceps brachii",
    2: "Anterior deltoid",
    3: "Serratus anterior",
    4: "Pectoralis major",
    5: "Triceps brachii",
    6: "Biceps femoris",
    7: "Gastrocnemius",
    8: "Anterior tibial",
    9: "Vastus lateralis",
    10: "Rectus abdominis",
    11: "Gluteus maximus",
    12: "Soleus",
    13: "Quadriceps",
    14: "Rear deltoid",
    15: "Brachialis",
    16: "Latissimus dorsi",
}

_muscle_map: dict[int, str] = {}  # filled on first use


def _ensure_muscle_map() -> None:
    """Fetch the wger muscle list once and cache it permanently."""
    global _muscle_map
    if _muscle_map:
        return
    try:
        resp = httpx.get(
            "https://wger.de/api/v2/muscle/?format=json&limit=100",
            timeout=5.0,
        )
        if resp.status_code == 200:
            for m in resp.json().get("results", []):
                name_en = m.get("name_en") or m.get("name") or ""
                if name_en:
                    _muscle_map[m["id"]] = name_en
    except (httpx.TimeoutException, httpx.ConnectError):
        pass
    # Merge fallback for any IDs we didn't get from the API
    for mid, mname in _MUSCLE_FALLBACK.items():
        _muscle_map.setdefault(mid, mname)


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
# Search-results cache (1 hr)
_exercise_cache: TTLCache = TTLCache(maxsize=1000, ttl=3600)
_stale_cache: dict[str, list] = {}

# Individual exercise-detail cache (24 hr)
_exercise_detail_cache: TTLCache = TTLCache(maxsize=500, ttl=86400)


@router.get("/search")
def search_exercises(
    q: str = Query(..., min_length=2, description="Search term"),
    limit: int = Query(10, ge=1, le=30, description="Max results"),
    current_user: User = Depends(get_current_user),
):
    """Search for exercises using the wger API with in-memory caching.

    Queries the wger exercise database by search term. Results are cached
    for 1 hour. If the external API is unreachable, stale cached results
    are returned when available; otherwise a 503 is raised.
    """
    cache_key = q.lower()

    # Return cached results if available
    if cache_key in _exercise_cache:
        return _exercise_cache[cache_key][:limit]

    # Call wger API
    try:
        response = httpx.get(
            "https://wger.de/api/v2/exercise/search/",
            params={"term": q, "language": "english", "format": "json"},
            timeout=5.0,
        )
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Non-200 status: {response.status_code}",
                request=response.request,
                response=response,
            )

        data = response.json()
        suggestions = data.get("suggestions", [])

        results = []
        for suggestion in suggestions:
            entry = suggestion.get("data", {})
            exercise_id = entry.get("id")
            results.append(
                {
                    "id": exercise_id,
                    "name": entry.get("name"),
                    "category": entry.get("category", []),
                    "muscles": [],
                    "muscles_secondary": [],
                }
            )

        # Update both caches
        _exercise_cache[cache_key] = results
        _stale_cache[cache_key] = results

        return results[:limit]

    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError):
        # Fallback to stale cache if available
        if cache_key in _stale_cache:
            return _stale_cache[cache_key][:limit]

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exercise search service is temporarily unavailable. Please try again later.",
        )


@router.get("/recent")
def get_recent_exercises(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the 8 most recently used exercise names for the current user.

    Queries workout sets joined with workouts, groups by exercise name,
    and orders by the most recent workout date descending.
    """
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


@router.get("/{exercise_name}/history")
def get_exercise_history(
    exercise_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the last 10 sessions where the current user logged a specific exercise.

    Performs a case-insensitive match on exercise name and returns
    date, sets, reps, weight, and workout ID for each entry.
    """
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