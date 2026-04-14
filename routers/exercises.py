import os
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Query, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from cachetools import TTLCache
import httpx

from database import get_db
from models.user import User
from models.workout import Workout, WorkoutSet
from auth.utils import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

EXERCISEDB_URL = os.getenv("EXERCISEDB_URL", "https://exercisedb-apiii.vercel.app")

# Bounded in-memory caches with automatic TTL eviction
_search_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)   # 1 hour
_detail_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)   # 1 hour

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
    if cache_key in _search_cache:
        return _search_cache[cache_key]
    
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
            _search_cache[cache_key] = normalized
            return normalized
        return []
        
    except Exception as e:
        logger.warning("Exercise search failed for q='%s': %s", q, e)
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

@router.get("/gif-proxy")
async def proxy_exercise_gif(url: str):
    """
    Proxies ExerciseDB GIF requests so Flutter clients don't need the API key.
    No authentication required (Image.network cannot send auth headers).
    """
    allowed_prefixes = (
        "https://v2.exercisedb.io/",
        EXERCISEDB_URL,
    )
    if not any(url.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail="Invalid image URL")

    async def stream_gif():
        # follow_redirects=True is critical — the CDN often redirects
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            try:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            except Exception as e:
                print(f"[ForgeFit] GIF proxy stream error for {url}: {e}")

    # Detect content-type from HEAD request first so Flutter doesn't reject it
    content_type = "image/gif"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            head = await client.head(url)
            ct = head.headers.get("content-type", "")
            if ct:
                content_type = ct.split(";")[0].strip()
    except Exception:
        pass  # Fall back to image/gif

    return StreamingResponse(
        content=stream_gif(),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{exercise_id}")
def get_exercise_by_id(
    exercise_id: str = Path(..., description="Exercise ID"),
    current_user: User = Depends(get_current_user),
):
    cache_key = f"exercise_{exercise_id}"
    if cache_key in _detail_cache:
        return _detail_cache[cache_key]
    
    try:
        response = httpx.get(
            f"{EXERCISEDB_URL}/api/v1/exercises/{exercise_id}",
            timeout=10.0
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("success") and "data" in data:
            normalized = _normalize_exercise(data["data"])
            _detail_cache[cache_key] = normalized
            return normalized
        raise HTTPException(status_code=404, detail="Exercise not found")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Exercise not found")
        logger.warning("Exercise detail fetch failed for id='%s': %s", exercise_id, e)
        raise HTTPException(status_code=503, detail="Exercise details unavailable")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Exercise detail fetch failed for id='%s': %s", exercise_id, e)
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