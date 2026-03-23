from schemas.workout import WorkoutSummary
from datetime import date

summary = {
    "id": 1,
    "user_id": 1,
    "date": date.today(),
    "notes": "Test notes",
    "name": "Test workout",
    "duration_seconds": 3600,
    "calories_burned": 250,
    "total_sets": 5,
    "total_volume_kg": 1500.0,
}

model = WorkoutSummary.model_validate(summary)
print(model.model_dump())
print("Calories burned in model:", model.calories_burned)
