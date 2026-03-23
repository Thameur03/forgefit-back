from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from database import get_db
from models.program import Program, ProgramDay, ProgramExercise
from models.user import User

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ProgramExerciseSchema(BaseModel):
    id: int
    exercise_name: str
    sets: int
    reps: int
    weight_kg: Optional[float]
    order_index: int

    class Config:
        from_attributes = True


class ProgramDaySchema(BaseModel):
    id: int
    day_number: int
    day_name: str
    exercises: list[ProgramExerciseSchema]

    class Config:
        from_attributes = True


class ProgramResponse(BaseModel):
    id: int
    name: str
    weeks: Optional[int]
    days_per_week: Optional[int]
    is_active: bool
    source_template: Optional[str]
    days: list[ProgramDaySchema]

    class Config:
        from_attributes = True


class ProgramSummary(BaseModel):
    id: int
    name: str
    weeks: Optional[int]
    days_per_week: Optional[int]
    is_active: bool
    source_template: Optional[str]

    class Config:
        from_attributes = True


class CreateProgramBody(BaseModel):
    name: str
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None


class AddExerciseBody(BaseModel):
    exercise_name: str
    sets: int = 3
    reps: int = 8
    weight_kg: Optional[float] = None
    order_index: int = 0


class UpdateExerciseBody(BaseModel):
    exercise_name: Optional[str] = None
    sets: Optional[int] = None
    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    order_index: Optional[int] = None


class UpdateProgramBody(BaseModel):
    name: Optional[str] = None
    weeks: Optional[int] = None
    days_per_week: Optional[int] = None


# ---------------------------------------------------------------------------
# Hardcoded Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "name": "Push Pull Legs",
        "slug": "push_pull_legs",
        "weeks": 6,
        "days_per_week": 6,
        "days": [
            {
                "day_number": 1,
                "day_name": "Push Day",
                "exercises": [
                    {"exercise_name": "Barbell Bench Press", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Incline Dumbbell Press", "sets": 3, "reps": 10, "order_index": 1},
                    {"exercise_name": "Overhead Press", "sets": 3, "reps": 10, "order_index": 2},
                    {"exercise_name": "Lateral Raises", "sets": 4, "reps": 15, "order_index": 3},
                    {"exercise_name": "Tricep Pushdown", "sets": 3, "reps": 12, "order_index": 4},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Pull Day",
                "exercises": [
                    {"exercise_name": "Deadlift", "sets": 4, "reps": 6, "order_index": 0},
                    {"exercise_name": "Pull Ups", "sets": 4, "reps": 8, "order_index": 1},
                    {"exercise_name": "Barbell Row", "sets": 3, "reps": 10, "order_index": 2},
                    {"exercise_name": "Face Pulls", "sets": 3, "reps": 15, "order_index": 3},
                    {"exercise_name": "Dumbbell Curl", "sets": 3, "reps": 12, "order_index": 4},
                ],
            },
            {
                "day_number": 3,
                "day_name": "Legs Day",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Romanian Deadlift", "sets": 3, "reps": 10, "order_index": 1},
                    {"exercise_name": "Leg Press", "sets": 3, "reps": 12, "order_index": 2},
                    {"exercise_name": "Leg Curl", "sets": 3, "reps": 12, "order_index": 3},
                    {"exercise_name": "Calf Raises", "sets": 4, "reps": 15, "order_index": 4},
                ],
            },
        ],
    },
    {
        "name": "Bro Split",
        "slug": "bro_split",
        "weeks": 8,
        "days_per_week": 5,
        "days": [
            {
                "day_number": 1,
                "day_name": "Chest Day",
                "exercises": [
                    {"exercise_name": "Barbell Bench Press", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Incline Dumbbell Press", "sets": 3, "reps": 10, "order_index": 1},
                    {"exercise_name": "Cable Fly", "sets": 3, "reps": 15, "order_index": 2},
                    {"exercise_name": "Dips", "sets": 3, "reps": 12, "order_index": 3},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Back Day",
                "exercises": [
                    {"exercise_name": "Deadlift", "sets": 4, "reps": 6, "order_index": 0},
                    {"exercise_name": "Pull Ups", "sets": 4, "reps": 8, "order_index": 1},
                    {"exercise_name": "Seated Cable Row", "sets": 3, "reps": 10, "order_index": 2},
                    {"exercise_name": "Lat Pulldown", "sets": 3, "reps": 12, "order_index": 3},
                ],
            },
            {
                "day_number": 3,
                "day_name": "Shoulders Day",
                "exercises": [
                    {"exercise_name": "Overhead Press", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Lateral Raises", "sets": 4, "reps": 15, "order_index": 1},
                    {"exercise_name": "Front Raises", "sets": 3, "reps": 12, "order_index": 2},
                    {"exercise_name": "Face Pulls", "sets": 3, "reps": 15, "order_index": 3},
                ],
            },
            {
                "day_number": 4,
                "day_name": "Arms Day",
                "exercises": [
                    {"exercise_name": "Barbell Curl", "sets": 4, "reps": 10, "order_index": 0},
                    {"exercise_name": "Hammer Curl", "sets": 3, "reps": 12, "order_index": 1},
                    {"exercise_name": "Tricep Pushdown", "sets": 4, "reps": 12, "order_index": 2},
                    {"exercise_name": "Skull Crushers", "sets": 3, "reps": 10, "order_index": 3},
                ],
            },
            {
                "day_number": 5,
                "day_name": "Legs Day",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Leg Press", "sets": 3, "reps": 12, "order_index": 1},
                    {"exercise_name": "Leg Curl", "sets": 3, "reps": 12, "order_index": 2},
                    {"exercise_name": "Calf Raises", "sets": 4, "reps": 15, "order_index": 3},
                ],
            },
        ],
    },
    {
        "name": "Full Body",
        "slug": "full_body",
        "weeks": 4,
        "days_per_week": 3,
        "days": [
            {
                "day_number": 1,
                "day_name": "Full Body A",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 3, "reps": 8, "order_index": 0},
                    {"exercise_name": "Barbell Bench Press", "sets": 3, "reps": 8, "order_index": 1},
                    {"exercise_name": "Barbell Row", "sets": 3, "reps": 8, "order_index": 2},
                    {"exercise_name": "Overhead Press", "sets": 3, "reps": 10, "order_index": 3},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Full Body B",
                "exercises": [
                    {"exercise_name": "Romanian Deadlift", "sets": 3, "reps": 8, "order_index": 0},
                    {"exercise_name": "Incline Dumbbell Press", "sets": 3, "reps": 10, "order_index": 1},
                    {"exercise_name": "Pull Ups", "sets": 3, "reps": 8, "order_index": 2},
                    {"exercise_name": "Lateral Raises", "sets": 3, "reps": 15, "order_index": 3},
                ],
            },
            {
                "day_number": 3,
                "day_name": "Full Body C",
                "exercises": [
                    {"exercise_name": "Deadlift", "sets": 3, "reps": 6, "order_index": 0},
                    {"exercise_name": "Dumbbell Bench Press", "sets": 3, "reps": 10, "order_index": 1},
                    {"exercise_name": "Lat Pulldown", "sets": 3, "reps": 10, "order_index": 2},
                    {"exercise_name": "Dumbbell Curl", "sets": 3, "reps": 12, "order_index": 3},
                ],
            },
        ],
    },
    {
        "name": "Upper Lower",
        "slug": "upper_lower",
        "weeks": 12,
        "days_per_week": 4,
        "days": [
            {
                "day_number": 1,
                "day_name": "Upper A",
                "exercises": [
                    {"exercise_name": "Barbell Bench Press", "sets": 4, "reps": 6, "order_index": 0},
                    {"exercise_name": "Barbell Row", "sets": 4, "reps": 6, "order_index": 1},
                    {"exercise_name": "Overhead Press", "sets": 3, "reps": 8, "order_index": 2},
                    {"exercise_name": "Pull Ups", "sets": 3, "reps": 8, "order_index": 3},
                    {"exercise_name": "Dumbbell Curl", "sets": 3, "reps": 12, "order_index": 4},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Lower A",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 4, "reps": 6, "order_index": 0},
                    {"exercise_name": "Romanian Deadlift", "sets": 3, "reps": 8, "order_index": 1},
                    {"exercise_name": "Leg Press", "sets": 3, "reps": 10, "order_index": 2},
                    {"exercise_name": "Leg Curl", "sets": 3, "reps": 10, "order_index": 3},
                    {"exercise_name": "Calf Raises", "sets": 4, "reps": 15, "order_index": 4},
                ],
            },
            {
                "day_number": 3,
                "day_name": "Upper B",
                "exercises": [
                    {"exercise_name": "Incline Dumbbell Press", "sets": 4, "reps": 8, "order_index": 0},
                    {"exercise_name": "Seated Cable Row", "sets": 4, "reps": 8, "order_index": 1},
                    {"exercise_name": "Lateral Raises", "sets": 4, "reps": 15, "order_index": 2},
                    {"exercise_name": "Tricep Pushdown", "sets": 3, "reps": 12, "order_index": 3},
                    {"exercise_name": "Hammer Curl", "sets": 3, "reps": 12, "order_index": 4},
                ],
            },
            {
                "day_number": 4,
                "day_name": "Lower B",
                "exercises": [
                    {"exercise_name": "Deadlift", "sets": 4, "reps": 5, "order_index": 0},
                    {"exercise_name": "Front Squat", "sets": 3, "reps": 8, "order_index": 1},
                    {"exercise_name": "Walking Lunges", "sets": 3, "reps": 12, "order_index": 2},
                    {"exercise_name": "Leg Extension", "sets": 3, "reps": 15, "order_index": 3},
                    {"exercise_name": "Calf Raises", "sets": 4, "reps": 15, "order_index": 4},
                ],
            },
        ],
    },
    {
        "name": "Powerlifting Peaking",
        "slug": "powerlifting_peaking",
        "weeks": 8,
        "days_per_week": 4,
        "days": [
            {
                "day_number": 1,
                "day_name": "Squat Focus",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 5, "reps": 5, "order_index": 0},
                    {"exercise_name": "Pause Squat", "sets": 3, "reps": 3, "order_index": 1},
                    {"exercise_name": "Leg Press", "sets": 3, "reps": 8, "order_index": 2},
                    {"exercise_name": "Leg Curl", "sets": 3, "reps": 10, "order_index": 3},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Bench Focus",
                "exercises": [
                    {"exercise_name": "Barbell Bench Press", "sets": 5, "reps": 5, "order_index": 0},
                    {"exercise_name": "Pause Bench Press", "sets": 3, "reps": 3, "order_index": 1},
                    {"exercise_name": "Tricep Pushdown", "sets": 4, "reps": 10, "order_index": 2},
                    {"exercise_name": "Lateral Raises", "sets": 3, "reps": 15, "order_index": 3},
                ],
            },
            {
                "day_number": 3,
                "day_name": "Deadlift Focus",
                "exercises": [
                    {"exercise_name": "Deadlift", "sets": 5, "reps": 3, "order_index": 0},
                    {"exercise_name": "Romanian Deadlift", "sets": 3, "reps": 6, "order_index": 1},
                    {"exercise_name": "Barbell Row", "sets": 4, "reps": 8, "order_index": 2},
                    {"exercise_name": "Pull Ups", "sets": 3, "reps": 8, "order_index": 3},
                ],
            },
            {
                "day_number": 4,
                "day_name": "Accessory Day",
                "exercises": [
                    {"exercise_name": "Overhead Press", "sets": 4, "reps": 6, "order_index": 0},
                    {"exercise_name": "Dumbbell Row", "sets": 4, "reps": 10, "order_index": 1},
                    {"exercise_name": "Dumbbell Curl", "sets": 3, "reps": 12, "order_index": 2},
                    {"exercise_name": "Skull Crushers", "sets": 3, "reps": 10, "order_index": 3},
                ],
            },
        ],
    },
    {
        "name": "Starting Strength",
        "slug": "starting_strength",
        "weeks": None,
        "days_per_week": 3,
        "days": [
            {
                "day_number": 1,
                "day_name": "Workout A",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 3, "reps": 5, "order_index": 0},
                    {"exercise_name": "Barbell Bench Press", "sets": 3, "reps": 5, "order_index": 1},
                    {"exercise_name": "Deadlift", "sets": 1, "reps": 5, "order_index": 2},
                ],
            },
            {
                "day_number": 2,
                "day_name": "Workout B",
                "exercises": [
                    {"exercise_name": "Barbell Squat", "sets": 3, "reps": 5, "order_index": 0},
                    {"exercise_name": "Overhead Press", "sets": 3, "reps": 5, "order_index": 1},
                    {"exercise_name": "Deadlift", "sets": 1, "reps": 5, "order_index": 2},
                ],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_program_or_404(program_id: int, db: Session) -> Program:
    program = db.query(Program).filter(Program.id == program_id).first()
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found")
    return program


def _assert_ownership(program: Program, current_user: User) -> None:
    if program.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/templates", status_code=status.HTTP_200_OK)
def get_templates():
    """Return all hardcoded program templates. No auth required."""
    return TEMPLATES


@router.post(
    "/from-template/{slug}",
    response_model=ProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_from_template(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a full program from a hardcoded template."""
    template = next((t for t in TEMPLATES if t["slug"] == slug), None)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    full_name = getattr(current_user, "full_name", None) or current_user.email
    program = Program(
        user_id=current_user.id,
        name=f"{full_name}'s {template['name']}",
        weeks=template["weeks"],
        days_per_week=template["days_per_week"],
        is_active=False,
        source_template=slug,
    )
    db.add(program)
    db.flush()  # get program.id without committing

    for day_data in template["days"]:
        day = ProgramDay(
            program_id=program.id,
            day_number=day_data["day_number"],
            day_name=day_data["day_name"],
        )
        db.add(day)
        db.flush()

        for ex_data in day_data["exercises"]:
            exercise = ProgramExercise(
                program_day_id=day.id,
                exercise_name=ex_data["exercise_name"],
                sets=ex_data["sets"],
                reps=ex_data["reps"],
                weight_kg=None,
                order_index=ex_data["order_index"],
            )
            db.add(exercise)

    db.commit()
    db.refresh(program)
    return program


@router.post("/", response_model=ProgramResponse, status_code=status.HTTP_201_CREATED)
def create_program(
    data: CreateProgramBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a blank program with empty days."""
    program = Program(
        user_id=current_user.id,
        name=data.name,
        weeks=data.weeks,
        days_per_week=data.days_per_week,
        is_active=False,
        source_template=None,
    )
    db.add(program)
    db.flush()

    days_count = data.days_per_week or 0
    for n in range(1, days_count + 1):
        day = ProgramDay(
            program_id=program.id,
            day_number=n,
            day_name=f"Day {n}",
        )
        db.add(day)

    db.commit()
    db.refresh(program)
    return program


@router.get("/", response_model=list[ProgramSummary], status_code=status.HTTP_200_OK)
def list_programs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all programs for the current user, ordered by id descending."""
    return (
        db.query(Program)
        .filter(Program.user_id == current_user.id)
        .order_by(Program.id.desc())
        .all()
    )


@router.get("/active", response_model=ProgramResponse, status_code=status.HTTP_200_OK)
def get_active_program(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the user's currently active program."""
    program = (
        db.query(Program)
        .filter(Program.user_id == current_user.id, Program.is_active.is_(True))
        .first()
    )
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active program")
    return program


@router.get("/{program_id}", response_model=ProgramResponse, status_code=status.HTTP_200_OK)
def get_program(
    program_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a specific program by ID."""
    program = _get_program_or_404(program_id, db)
    _assert_ownership(program, current_user)
    return program


@router.put("/{program_id}", response_model=ProgramResponse, status_code=status.HTTP_200_OK)
def update_program(
    program_id: int,
    data: UpdateProgramBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update non-None fields of a program."""
    program = _get_program_or_404(program_id, db)
    _assert_ownership(program, current_user)

    if data.name is not None:
        program.name = data.name
    if data.weeks is not None:
        program.weeks = data.weeks
    if data.days_per_week is not None:
        program.days_per_week = data.days_per_week

    db.commit()
    db.refresh(program)
    return program


@router.delete("/{program_id}", status_code=status.HTTP_200_OK)
def delete_program(
    program_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a program and cascade to days and exercises."""
    program = _get_program_or_404(program_id, db)
    _assert_ownership(program, current_user)
    db.delete(program)
    db.commit()
    return {"message": "Program deleted"}


@router.put(
    "/{program_id}/activate",
    response_model=ProgramResponse,
    status_code=status.HTTP_200_OK,
)
def activate_program(
    program_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set one program as active; deactivate all others for this user."""
    program = _get_program_or_404(program_id, db)
    _assert_ownership(program, current_user)

    # Deactivate all user programs
    db.query(Program).filter(Program.user_id == current_user.id).update(
        {Program.is_active: False}
    )
    program.is_active = True
    db.commit()
    db.refresh(program)
    return program


@router.post(
    "/days/{day_id}/exercises",
    response_model=ProgramExerciseSchema,
    status_code=status.HTTP_201_CREATED,
)
def add_exercise_to_day(
    day_id: int,
    data: AddExerciseBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add an exercise to a program day."""
    day = db.query(ProgramDay).filter(ProgramDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    program = db.query(Program).filter(Program.id == day.program_id).first()
    if program is None or program.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    exercise = ProgramExercise(
        program_day_id=day_id,
        exercise_name=data.exercise_name,
        sets=data.sets,
        reps=data.reps,
        weight_kg=data.weight_kg,
        order_index=data.order_index,
    )
    db.add(exercise)
    db.commit()
    db.refresh(exercise)
    return exercise


@router.put(
    "/days/{day_id}/exercises/{exercise_id}",
    response_model=ProgramExerciseSchema,
    status_code=status.HTTP_200_OK,
)
def update_exercise(
    day_id: int,
    exercise_id: int,
    data: UpdateExerciseBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an exercise in a program day."""
    day = db.query(ProgramDay).filter(ProgramDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    program = db.query(Program).filter(Program.id == day.program_id).first()
    if program is None or program.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    exercise = (
        db.query(ProgramExercise)
        .filter(ProgramExercise.id == exercise_id, ProgramExercise.program_day_id == day_id)
        .first()
    )
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")

    if data.exercise_name is not None:
        exercise.exercise_name = data.exercise_name
    if data.sets is not None:
        exercise.sets = data.sets
    if data.reps is not None:
        exercise.reps = data.reps
    if data.weight_kg is not None:
        exercise.weight_kg = data.weight_kg
    if data.order_index is not None:
        exercise.order_index = data.order_index

    db.commit()
    db.refresh(exercise)
    return exercise


@router.delete(
    "/days/{day_id}/exercises/{exercise_id}",
    status_code=status.HTTP_200_OK,
)
def delete_exercise(
    day_id: int,
    exercise_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove an exercise from a program day."""
    day = db.query(ProgramDay).filter(ProgramDay.id == day_id).first()
    if day is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Day not found")
    program = db.query(Program).filter(Program.id == day.program_id).first()
    if program is None or program.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    exercise = (
        db.query(ProgramExercise)
        .filter(ProgramExercise.id == exercise_id, ProgramExercise.program_day_id == day_id)
        .first()
    )
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")

    db.delete(exercise)
    db.commit()
    return {"message": "Exercise removed"}
