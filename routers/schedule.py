from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from auth.utils import get_current_user
from models.user import User
from models.schedule import ScheduledWorkout
from models.program import Program, ProgramDay
from schemas.schedule import (
    ScheduledWorkoutCreate,
    ScheduledWorkoutResponse,
)
from schemas.program import ProgramExerciseSchema

router = APIRouter()


def _build_response(sw: ScheduledWorkout) -> ScheduledWorkoutResponse:
    return ScheduledWorkoutResponse(
        id=sw.id,
        user_id=sw.user_id,
        program_id=sw.program_id,
        program_day_id=sw.program_day_id,
        scheduled_date=sw.scheduled_date,
        day_name=sw.program_day.day_name,
        program_name=sw.program.name,
        exercises=[
            ProgramExerciseSchema(
                id=ex.id,
                exercise_name=ex.exercise_name,
                exercise_id=ex.exercise_id,
                sets=ex.sets,
                reps=ex.reps,
                weight_kg=ex.weight_kg,
                order_index=ex.order_index,
            )
            for ex in sw.program_day.exercises
        ],
        status=sw.status,
        completed_at=sw.completed_at,
        linkage_trustworthy=sw.linkage_trustworthy,
    )


@router.post("/", response_model=ScheduledWorkoutResponse)
def schedule_workout(
    body: ScheduledWorkoutCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify program_day belongs to an active program owned by this user
    day = db.query(ProgramDay).filter(ProgramDay.id == body.program_day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Program day not found")

    program = db.query(Program).filter(
        Program.id == day.program_id,
        Program.user_id == current_user.id,
        Program.is_active == True,
    ).first()
    if not program:
        raise HTTPException(
            status_code=403,
            detail="Program day does not belong to your active program",
        )

    # Stable upsert: preserve identity so completed workouts retain an exact
    # scheduled_workout_id link for program-execution analytics.
    existing = db.query(ScheduledWorkout).filter(
        ScheduledWorkout.user_id == current_user.id,
        ScheduledWorkout.scheduled_date == body.scheduled_date,
    ).first()
    if existing:
        existing.program_id = program.id
        existing.program_day_id = body.program_day_id
        existing.status = "planned"
        existing.completed_at = None
        existing.linkage_trustworthy = True
        sw = existing
    else:
        sw = ScheduledWorkout(
            user_id=current_user.id,
            program_id=program.id,
            program_day_id=body.program_day_id,
            scheduled_date=body.scheduled_date,
            status="planned",
            linkage_trustworthy=True,
        )
        db.add(sw)
    db.commit()
    db.refresh(sw)
    return _build_response(sw)


@router.get("/today", response_model=ScheduledWorkoutResponse)
def get_today_scheduled(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    today = date_type.today()
    sw = db.query(ScheduledWorkout).filter(
        ScheduledWorkout.user_id == current_user.id,
        ScheduledWorkout.scheduled_date == today,
        ScheduledWorkout.status != "cancelled",
    ).first()
    if not sw:
        raise HTTPException(status_code=404, detail="No scheduled workout for today")
    return _build_response(sw)


@router.get("/date/{date_str}", response_model=ScheduledWorkoutResponse)
def get_scheduled_for_date(
    date_str: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        target = date_type.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")

    sw = db.query(ScheduledWorkout).filter(
        ScheduledWorkout.user_id == current_user.id,
        ScheduledWorkout.scheduled_date == target,
        ScheduledWorkout.status != "cancelled",
    ).first()
    if not sw:
        raise HTTPException(status_code=404, detail="No scheduled workout for this date")
    return _build_response(sw)


@router.get("/month", response_model=list[ScheduledWorkoutResponse])
def get_scheduled_for_month(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import extract
    rows = db.query(ScheduledWorkout).filter(
        ScheduledWorkout.user_id == current_user.id,
        extract("year", ScheduledWorkout.scheduled_date) == year,
        extract("month", ScheduledWorkout.scheduled_date) == month,
        ScheduledWorkout.status != "cancelled",
    ).all()
    return [_build_response(r) for r in rows]


@router.delete("/{scheduled_id}", status_code=204)
def delete_scheduled_workout(
    scheduled_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sw = db.query(ScheduledWorkout).filter(
        ScheduledWorkout.id == scheduled_id,
        ScheduledWorkout.user_id == current_user.id,
    ).first()
    if not sw:
        raise HTTPException(status_code=404, detail="Scheduled workout not found")
    # Preserve cancellation history while excluding it from eligible planned
    # opportunities. Hard deletion would silently change old denominators.
    sw.status = "cancelled"
    sw.completed_at = None
    db.commit()
