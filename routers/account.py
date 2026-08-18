"""Authenticated customer account deletion."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.analytics_event import AnalyticsEvent
from models.nutrition import NutritionLog
from models.program import Program
from models.schedule import ScheduledWorkout
from models.token import RevokedToken
from models.user import User
from models.workout import Workout

router = APIRouter()
logger = logging.getLogger(__name__)


def _delete_user_owned_records(db: Session, user: User) -> None:
    """Stage deletion of every current user-owned model in FK-safe order.

    The caller owns the transaction. ORM cascades remove workout sets and the
    program day/exercise hierarchy; direct user-owned rows are explicitly
    filtered by the authenticated user's primary key.
    """

    user_id = user.id

    # Scheduling references both programs and program days, so it must go first.
    db.query(ScheduledWorkout).filter(
        ScheduledWorkout.user_id == user_id
    ).delete(synchronize_session=False)

    # ORM delete cascades: Program -> ProgramDay -> ProgramExercise.
    for program in db.query(Program).filter(Program.user_id == user_id).all():
        db.delete(program)

    # ORM delete cascades: Workout -> WorkoutSet.
    for workout in db.query(Workout).filter(Workout.user_id == user_id).all():
        db.delete(workout)

    db.query(NutritionLog).filter(NutritionLog.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(RevokedToken).filter(RevokedToken.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(AnalyticsEvent).filter(AnalyticsEvent.user_id == user_id).delete(
        synchronize_session=False
    )

    # Persist dependent deletes before deleting User. There are deliberately no
    # broad User relationships in the model, so an explicit flush establishes
    # the required FK order while remaining inside the same transaction.
    db.flush()

    # Verification/reset state and profile data are columns on the user row.
    db.delete(user)
    db.flush()


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
    """Permanently delete only the authenticated user's current-schema data."""

    try:
        _delete_user_owned_records(db, current_user)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Account deletion transaction rolled back")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account deletion failed; no data was deleted",
        ) from exc

    return {
        "message": "Account and all associated data have been permanently deleted."
    }
