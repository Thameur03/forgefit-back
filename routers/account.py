"""
Account management router — covers the Play Store-required account deletion flow.

DELETE /account/me
  • Deletes all user data (workouts, nutrition logs, schedules, programs, tokens, user record).
  • Hard delete: no soft-delete / grace period by default.
  • Requires a valid Bearer token (authenticated user only).
  • Returns 200 on success so the client can clear local storage and navigate to /login.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from models.user import User
from auth.utils import get_current_user

router = APIRouter()


@router.delete(
    "/me",
    status_code=status.HTTP_200_OK,
    summary="Delete the authenticated user's account and all associated data",
    tags=["Account"],
)
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently delete the authenticated user's account and cascade-delete
    all associated records (workouts, nutrition logs, scheduled sessions,
    active programmes, revoked tokens, analytics events).

    This action is irreversible.  The client is expected to:
    1. Receive a 200 response.
    2. Clear all locally stored tokens (flutter_secure_storage).
    3. Navigate to the login screen.

    Data deleted:
    - Revoked token records (models.token.RevokedToken)
    - Analytics events (models.analytics_event.AnalyticsEvent)
    - Scheduled sessions (models.schedule.ScheduledSession)
    - Nutrition logs (models.nutrition.NutritionEntry)
    - Workout sessions and their exercises (models.workout)
    - Active programmes and programme workouts (models.program)
    - The user record itself (models.user.User)
    """
    user_id = current_user.id

    # ── Delete in dependency order (children before parent) ──────────────────
    # Use raw DELETE statements for performance; all tables have user_id FK.

    db.execute(text("DELETE FROM revoked_tokens WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM analytics_events WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM scheduled_sessions WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM nutrition_entries WHERE user_id = :uid"), {"uid": user_id})

    # Workout exercises → workout sessions
    db.execute(
        text(
            "DELETE FROM workout_exercises "
            "WHERE workout_session_id IN "
            "(SELECT id FROM workout_sessions WHERE user_id = :uid)"
        ),
        {"uid": user_id},
    )
    db.execute(text("DELETE FROM workout_sessions WHERE user_id = :uid"), {"uid": user_id})

    # Programme workouts → programmes
    db.execute(
        text(
            "DELETE FROM program_workouts "
            "WHERE program_id IN "
            "(SELECT id FROM programs WHERE user_id = :uid)"
        ),
        {"uid": user_id},
    )
    db.execute(text("DELETE FROM programs WHERE user_id = :uid"), {"uid": user_id})

    # Finally delete the user row
    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})

    db.commit()

    return {"message": "Account and all associated data have been permanently deleted."}
