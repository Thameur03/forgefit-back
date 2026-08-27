import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from models.user import User
from models.workout import Workout, WorkoutSet
from models.program import Program
from models.nutrition import NutritionLog
from schemas.ai_coach import (
    AICoachRecommendation,
    AICoachWarning,
    AICoachScoreBreakdown,
    AICoachSummaryResponse,
    DataState,
)


# ---------------------------------------------------------------------------
# Muscle classification — duplicated from routers/stats.py
# TODO: move shared muscle classification logic to services/stats_helpers.py
# ---------------------------------------------------------------------------

MUSCLE_MAP: dict[str, str] = {
    # Chest
    "incline bench press": "Chest",
    "decline bench press": "Chest",
    "bench press": "Chest",
    "chest fly": "Chest",
    "chest press": "Chest",
    "cable crossover": "Chest",
    "pec deck": "Chest",
    "push-up": "Chest",
    "pushup": "Chest",
    "dips": "Chest",
    # Back
    "lat pulldown": "Back",
    "seated row": "Back",
    "cable row": "Back",
    "t-bar row": "Back",
    "barbell row": "Back",
    "dumbbell row": "Back",
    "pull-up": "Back",
    "pullup": "Back",
    "chin-up": "Back",
    "chinup": "Back",
    "hyperextension": "Back",
    "deadlift": "Back",
    "row": "Back",
    # Shoulders
    "shoulder press": "Shoulders",
    "arnold press": "Shoulders",
    "overhead press": "Shoulders",
    "military press": "Shoulders",
    "lateral raise": "Shoulders",
    "front raise": "Shoulders",
    "face pull": "Shoulders",
    "upright row": "Shoulders",
    "shrug": "Shoulders",
    # Biceps
    "preacher curl": "Biceps",
    "concentration curl": "Biceps",
    "hammer curl": "Biceps",
    "barbell curl": "Biceps",
    "incline curl": "Biceps",
    "curl": "Biceps",
    # Triceps
    "skull crusher": "Triceps",
    "close grip bench": "Triceps",
    "tricep pushdown": "Triceps",
    "tricep extension": "Triceps",
    "overhead tricep": "Triceps",
    "tricep": "Triceps",
    # Abs
    "russian twist": "Abs",
    "leg raise": "Abs",
    "hanging knee": "Abs",
    "cable crunch": "Abs",
    "crunch": "Abs",
    "sit-up": "Abs",
    "situp": "Abs",
    "plank": "Abs",
    "ab rollout": "Abs",
    # Quads
    "leg extension": "Quads",
    "hack squat": "Quads",
    "front squat": "Quads",
    "leg press": "Quads",
    "lunge": "Quads",
    "step-up": "Quads",
    "squat": "Quads",
    # Hamstrings
    "leg curl": "Hamstrings",
    "romanian deadlift": "Hamstrings",
    "rdl": "Hamstrings",
    "good morning": "Hamstrings",
    "nordic curl": "Hamstrings",
    "stiff leg": "Hamstrings",
    # Glutes
    "hip thrust": "Glutes",
    "glute bridge": "Glutes",
    "glute kickback": "Glutes",
    "cable kickback": "Glutes",
    # Calves
    "seated calf": "Calves",
    "standing calf": "Calves",
    "calf raise": "Calves",
    "calf press": "Calves",
}

_SORTED_KEYS = sorted(MUSCLE_MAP.keys(), key=len, reverse=True)


def classify_muscle(exercise_name: str) -> str:
    """Return the canonical muscle group for the given exercise name."""
    lower = exercise_name.lower()
    for key in _SORTED_KEYS:
        if key in lower:
            return MUSCLE_MAP[key]
    return "Other"


# ---------------------------------------------------------------------------
# AI Coach Engine
# ---------------------------------------------------------------------------


class AICoachEngine:
    """Rule-based explainable AI engine that analyses workout and nutrition
    data and returns a Weekly Readiness Score with sub-scores, confidence,
    recommendations, warnings, and a next best action."""

    def __init__(self, db: Session, user: User, days: int = 7):
        self.db = db
        self.user = user
        self.days = days

    # ── Public API ────────────────────────────────────────────────────────

    def generate_summary(self) -> AICoachSummaryResponse:
        # 1. Period calculation
        period_end = date.today()
        period_start = period_end - timedelta(days=self.days - 1)

        previous_period_end = period_start - timedelta(days=1)
        previous_period_start = previous_period_end - timedelta(days=self.days - 1)

        # 2–3. Workout data
        current_workouts = self._query_workouts(period_start, period_end)
        previous_workouts = self._query_workouts(previous_period_start, previous_period_end)

        current_sets = self._query_sets(current_workouts)
        previous_sets = self._query_sets(previous_workouts)

        # 4. Nutrition data
        nutrition_logs = self._query_nutrition(period_start, period_end)

        # 5. Active program
        active_program = self._query_active_program()

        # 6. Feature extraction
        features = self._extract_features(
            current_workouts, previous_workouts,
            current_sets, previous_sets,
            nutrition_logs, active_program,
        )

        # 7. Domain scores + breakdown
        training_score, nutrition_score, recovery_score, breakdown = (
            self._calculate_scores(features)
        )

        # Clamp
        training_score = max(0, min(100, round(training_score)))
        nutrition_score = max(0, min(100, round(nutrition_score)))
        recovery_score = max(0, min(100, round(recovery_score)))

        overall_score = round(
            training_score * 0.4
            + nutrition_score * 0.35
            + recovery_score * 0.25
        )
        overall_score = max(0, min(100, overall_score))

        # ── Overall score caps for serious issues ─────────────────────
        vcp_cap = features["volume_change_percent"]
        ppkg_cap = features["protein_per_kg"]
        cv_cap = features["calorie_cv"]

        if vcp_cap is not None and vcp_cap > 100:
            overall_score = min(overall_score, 75)
        if ppkg_cap is not None and ppkg_cap < 1.0:
            overall_score = min(overall_score, 70)
        if cv_cap is not None and cv_cap > 0.35:
            overall_score = min(overall_score, 75)
        # Combined cap: volume spike + low protein
        if (
            vcp_cap is not None and vcp_cap > 50
            and ppkg_cap is not None and ppkg_cap < 1.2
        ):
            overall_score = min(overall_score, 65)
        # Combined-risk penalties on overall
        if (
            vcp_cap is not None and vcp_cap > 50
            and ppkg_cap is not None and ppkg_cap < 1.2
        ):
            overall_score = max(0, overall_score - 5)
        if (
            ppkg_cap is not None and ppkg_cap < 1.2
            and cv_cap is not None and cv_cap > 0.25
        ):
            overall_score = max(0, overall_score - 5)

        overall_score = max(0, min(100, overall_score))

        readiness_label = self._readiness_label(overall_score)

        # ── Debug logging ─────────────────────────────────────────────
        # Exact health/fitness metrics remain only in the authenticated response.

        # 8. Recommendations + warnings
        recommendations, warnings = self._generate_recommendations_and_warnings(features)
        recommendations.sort(key=lambda r: r.impact, reverse=True)
        recommendations = recommendations[:5]

        # 9. Confidence
        confidence, confidence_reason = self._estimate_confidence(features)

        # 10. Missing data
        missing_data = features["missing_data"]

        # 11. Data state — determines summary tone and UI presentation
        data_state = self._derive_data_state(features)
        has_sufficient_data = data_state == "sufficient"

        # Summary text (data-state aware)
        summary = self._generate_summary_text(data_state, features, readiness_label)

        # Next best action (data-state aware)
        next_best_action = self._generate_next_best_action(
            recommendations, data_state, features
        )

        return AICoachSummaryResponse(
            generated_at=datetime.now(timezone.utc),
            period_days=self.days,
            overall_score=overall_score,
            training_score=training_score,
            nutrition_score=nutrition_score,
            recovery_score=recovery_score,
            readiness_label=readiness_label,
            confidence=confidence,
            confidence_reason=confidence_reason,
            missing_data=missing_data,
            data_state=data_state,
            has_sufficient_data=has_sufficient_data,
            summary=summary,
            recommendations=recommendations,
            warnings=warnings,
            next_best_action=next_best_action,
            score_breakdown=breakdown,
            workouts_this_period=features["workouts_this_period"],
            workouts_previous_period=features["workouts_previous_period"],
            weekly_volume_kg=round(features["weekly_volume_kg"], 2),
            previous_weekly_volume_kg=round(features["previous_weekly_volume_kg"], 2),
            volume_change_percent=(
                round(features["volume_change_percent"], 1)
                if features["volume_change_percent"] is not None
                else None
            ),
            active_program_name=(
                active_program.name if active_program else None
            ),
            active_program_days_per_week=(
                active_program.days_per_week if active_program else None
            ),
            adherence_percent=(
                round(features["adherence_percent"], 1)
                if features["adherence_percent"] is not None
                else None
            ),
            average_daily_calories=round(features["average_daily_calories"], 1),
            average_daily_protein_g=round(features["average_daily_protein_g"], 1),
            protein_per_kg=(
                round(features["protein_per_kg"], 2)
                if features["protein_per_kg"] is not None
                else None
            ),
            nutrition_logging_consistency_percent=round(
                features["logging_consistency_percent"], 1
            ),
            calorie_coefficient_of_variation=(
                round(features["calorie_cv"], 2)
                if features["calorie_cv"] is not None
                else None
            ),
        )

    # ── Data queries ──────────────────────────────────────────────────────

    def _query_workouts(self, start: date, end: date) -> list:
        return (
            self.db.query(Workout)
            .filter(
                Workout.user_id == self.user.id,
                Workout.date >= start,
                Workout.date <= end,
            )
            .all()
        )

    def _query_sets(self, workouts: list) -> list:
        if not workouts:
            return []
        workout_ids = [w.id for w in workouts]
        return (
            self.db.query(WorkoutSet)
            .filter(WorkoutSet.workout_id.in_(workout_ids))
            .all()
        )

    def _query_nutrition(self, start: date, end: date) -> list:
        return (
            self.db.query(NutritionLog)
            .filter(
                NutritionLog.user_id == self.user.id,
                NutritionLog.date >= start,
                NutritionLog.date <= end,
            )
            .all()
        )

    def _query_active_program(self):
        return (
            self.db.query(Program)
            .filter(
                Program.user_id == self.user.id,
                Program.is_active.is_(True),
            )
            .first()
        )

    # ── Feature extraction ────────────────────────────────────────────────

    def _extract_features(
        self,
        current_workouts, previous_workouts,
        current_sets, previous_sets,
        nutrition_logs, active_program,
    ) -> dict:
        missing_data: List[str] = []

        # --- Workout features ---
        workouts_this_period = len(current_workouts)
        workouts_previous_period = len(previous_workouts)

        if workouts_this_period == 0:
            missing_data.append("workouts")

        weekly_volume_kg = sum(
            (s.sets or 0) * (s.reps or 0) * (s.weight_kg or 0.0)
            for s in current_sets
        )
        previous_weekly_volume_kg = sum(
            (s.sets or 0) * (s.reps or 0) * (s.weight_kg or 0.0)
            for s in previous_sets
        )

        if previous_weekly_volume_kg > 0:
            volume_change_percent = (
                (weekly_volume_kg - previous_weekly_volume_kg)
                / previous_weekly_volume_kg
                * 100
            )
        elif weekly_volume_kg > 0:
            # Previous period had zero volume → treat as major spike
            volume_change_percent = 999.0
        else:
            volume_change_percent = None

        # Muscle distribution
        sets_by_muscle: dict[str, int] = defaultdict(int)
        total_sets = 0
        for s in current_sets:
            muscle = classify_muscle(s.exercise_name)
            set_count = s.sets or 0
            sets_by_muscle[muscle] += set_count
            total_sets += set_count

        top_muscle = ""
        top_muscle_sets = 0
        lowest_muscle = ""
        lowest_muscle_sets = float("inf")
        top_muscle_share_percent = 0.0

        if sets_by_muscle:
            for muscle, count in sets_by_muscle.items():
                if count > top_muscle_sets:
                    top_muscle = muscle
                    top_muscle_sets = count
                if count < lowest_muscle_sets:
                    lowest_muscle = muscle
                    lowest_muscle_sets = count
            if total_sets > 0:
                top_muscle_share_percent = (top_muscle_sets / total_sets) * 100

        # Active program adherence
        adherence_percent: Optional[float] = None
        if active_program and active_program.days_per_week:
            adherence_percent = min(
                (workouts_this_period / active_program.days_per_week) * 100,
                100.0,
            )
        else:
            missing_data.append("active_program")

        # --- Nutrition features ---
        if not nutrition_logs:
            missing_data.append("nutrition_logs")

        # Group by date for daily aggregates
        daily_nutrition: dict[date, dict] = defaultdict(
            lambda: {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
        )
        for log in nutrition_logs:
            daily_nutrition[log.date]["calories"] += float(log.calories or 0)
            daily_nutrition[log.date]["protein"] += float(log.protein_g or 0)
            daily_nutrition[log.date]["carbs"] += float(log.carbs_g or 0)
            daily_nutrition[log.date]["fat"] += float(log.fat_g or 0)

        logged_days = len(daily_nutrition)
        logging_consistency_percent = (logged_days / self.days) * 100 if self.days > 0 else 0.0

        if logged_days > 0:
            total_cal = sum(d["calories"] for d in daily_nutrition.values())
            total_pro = sum(d["protein"] for d in daily_nutrition.values())
            average_daily_calories = total_cal / logged_days
            average_daily_protein_g = total_pro / logged_days
        else:
            average_daily_calories = 0.0
            average_daily_protein_g = 0.0

        # Protein per kg
        weight = getattr(self.user, "weight_kg", None)
        if weight and weight > 0:
            protein_per_kg = average_daily_protein_g / weight
        else:
            protein_per_kg = None
            if "bodyweight" not in missing_data:
                missing_data.append("bodyweight")

        # Calorie CV
        calorie_cv: Optional[float] = None
        if logged_days >= 3:
            daily_cals = [d["calories"] for d in daily_nutrition.values()]
            mean_cal = sum(daily_cals) / len(daily_cals)
            if mean_cal > 0:
                variance = sum((c - mean_cal) ** 2 for c in daily_cals) / len(daily_cals)
                std_dev = math.sqrt(variance)
                calorie_cv = std_dev / mean_cal

        return {
            "workouts_this_period": workouts_this_period,
            "workouts_previous_period": workouts_previous_period,
            "weekly_volume_kg": weekly_volume_kg,
            "previous_weekly_volume_kg": previous_weekly_volume_kg,
            "volume_change_percent": volume_change_percent,
            "total_sets": total_sets,
            "sets_by_muscle": dict(sets_by_muscle),
            "top_muscle": top_muscle,
            "top_muscle_sets": top_muscle_sets,
            "top_muscle_share_percent": top_muscle_share_percent,
            "lowest_muscle": lowest_muscle,
            "active_program": active_program,
            "adherence_percent": adherence_percent,
            "logged_days": logged_days,
            "logging_consistency_percent": logging_consistency_percent,
            "average_daily_calories": average_daily_calories,
            "average_daily_protein_g": average_daily_protein_g,
            "protein_per_kg": protein_per_kg,
            "calorie_cv": calorie_cv,
            "missing_data": missing_data,
            "has_bodyweight": weight is not None and weight > 0,
        }

    # ── Scoring ───────────────────────────────────────────────────────────

    def _calculate_scores(self, f: dict):
        training = 100.0
        nutrition = 100.0
        recovery = 100.0
        breakdown = AICoachScoreBreakdown()

        # --- Training deductions ---
        if f["workouts_this_period"] == 0:
            training -= 20
            breakdown.no_workouts_deduction = 20.0

        if f["adherence_percent"] is not None:
            if f["adherence_percent"] < 50:
                training -= 15
                breakdown.adherence_deduction = 15.0
            elif f["adherence_percent"] < 80:
                training -= 8
                breakdown.adherence_deduction = 8.0
        else:
            breakdown.missing_active_program_note = True

        if f["total_sets"] >= 10 and f["top_muscle_share_percent"] > 50:
            training -= 8
            breakdown.muscle_imbalance_deduction = 8.0

        # --- Training deduction for volume spike (load management) ---
        vcp_train = f["volume_change_percent"]
        if vcp_train is not None and vcp_train > 10:
            if vcp_train > 100:
                training -= 15
                breakdown.volume_spike_training_deduction = 15.0
            elif vcp_train > 50:
                training -= 10
                breakdown.volume_spike_training_deduction = 10.0
            elif vcp_train > 25:
                training -= 7
                breakdown.volume_spike_training_deduction = 7.0
            elif vcp_train > 10:
                training -= 3
                breakdown.volume_spike_training_deduction = 3.0

        # --- Recovery deductions (v2 — stronger tiers) ---
        vcp = f["volume_change_percent"]
        if vcp is not None and vcp > 10:
            if vcp > 100:
                recovery -= 30
                breakdown.volume_spike_deduction = 30.0
            elif vcp > 50:
                recovery -= 22
                breakdown.volume_spike_deduction = 22.0
            elif vcp > 25:
                recovery -= 15
                breakdown.volume_spike_deduction = 15.0
            elif vcp > 10:
                recovery -= 8
                breakdown.volume_spike_deduction = 8.0

        # --- Nutrition deductions (v2 — stronger tiers) ---
        lcp = f["logging_consistency_percent"]
        if lcp < 50:
            nutrition -= 10
            breakdown.low_logging_deduction = 10.0
        elif lcp < 80:
            nutrition -= 5
            breakdown.low_logging_deduction = 5.0

        ppkg = f["protein_per_kg"]
        if ppkg is not None:
            if ppkg < 1.0:
                nutrition -= 25
                breakdown.low_protein_deduction = 25.0
            elif ppkg < 1.2:
                nutrition -= 18
                breakdown.low_protein_deduction = 18.0
            elif ppkg < 1.6:
                nutrition -= 10
                breakdown.low_protein_deduction = 10.0
        else:
            breakdown.missing_weight_note = True

        cv = f["calorie_cv"]
        if cv is not None:
            if cv > 0.35:
                nutrition -= 18
                breakdown.calorie_cv_deduction = 18.0
            elif cv > 0.25:
                nutrition -= 12
                breakdown.calorie_cv_deduction = 12.0

        # --- Combined-risk penalties ---
        # Recovery + nutrition risk: high volume spike with low protein
        if (
            vcp is not None and vcp > 50
            and ppkg is not None and ppkg < 1.2
        ):
            recovery -= 10

        # Bad nutrition quality: low protein + unstable calories
        if (
            ppkg is not None and ppkg < 1.2
            and cv is not None and cv > 0.25
        ):
            nutrition -= 8

        return training, nutrition, recovery, breakdown

    @staticmethod
    def _readiness_label(score: int) -> str:
        if score >= 85:
            return "Excellent"
        if score >= 70:
            return "Good"
        if score >= 50:
            return "Moderate"
        return "Needs Attention"

    # ── Recommendations & warnings ────────────────────────────────────────

    def _generate_recommendations_and_warnings(self, f: dict):
        recs: List[AICoachRecommendation] = []
        warns: List[AICoachWarning] = []

        # -- No workouts --
        if f["workouts_this_period"] == 0:
            recs.append(AICoachRecommendation(
                title="Restart your training rhythm",
                reason="No workouts were logged during this period.",
                action="Schedule one short full-body workout this week.",
                priority="high",
                category="workout",
                impact=10,
                metric="workouts_this_period",
            ))

        # -- Low adherence --
        if f["adherence_percent"] is not None and f["adherence_percent"] < 80:
            ap = f["active_program"]
            planned = ap.days_per_week if ap else "?"
            impact = 8 if f["adherence_percent"] < 50 else 5
            priority = "high" if impact >= 8 else "medium"
            recs.append(AICoachRecommendation(
                title="Follow your active program more consistently",
                reason=(
                    f"You completed {f['workouts_this_period']} workouts "
                    f"out of {planned} planned sessions."
                ),
                action="Complete your next planned workout before adding extra sessions.",
                priority=priority,
                category="workout",
                impact=impact,
                metric="adherence",
            ))

        # -- Volume spike --
        vcp = f["volume_change_percent"]
        if vcp is not None and vcp > 10:
            if vcp > 50:
                warn_priority = "high"
                warn_title = "Training volume increased sharply"
                warn_detail = (
                    f"Your total workout volume increased by {round(vcp, 1)}% "
                    f"compared with the previous period. Avoid adding more sets "
                    f"until recovery is stable."
                )
            elif vcp > 25:
                warn_priority = "high"
                warn_title = "Training volume increased quickly"
                warn_detail = (
                    f"Your training volume increased by {round(vcp, 1)}% "
                    f"compared with the previous period."
                )
            else:
                warn_priority = "medium"
                warn_title = "Training volume increased"
                warn_detail = (
                    f"Your training volume increased by {round(vcp, 1)}% "
                    f"compared with the previous period."
                )

            warns.append(AICoachWarning(
                code="volume_spike",
                title=warn_title,
                detail=warn_detail,
                priority=warn_priority,
            ))

            rec_impact = 10 if vcp > 50 else (8 if vcp > 25 else 6)
            recs.append(AICoachRecommendation(
                title="Avoid increasing volume further",
                reason=f"Your total training volume increased by {round(vcp, 1)}%.",
                action="Keep your next session moderate and avoid adding extra sets.",
                priority="high" if rec_impact >= 8 else "medium",
                category="recovery",
                impact=rec_impact,
                metric="volume_change_percent",
            ))

        # -- Muscle imbalance --
        if f["total_sets"] >= 10 and f["top_muscle_share_percent"] > 50:
            recs.append(AICoachRecommendation(
                title="Balance your training split",
                reason=(
                    f"{f['top_muscle']} represents {round(f['top_muscle_share_percent'], 1)}% "
                    f"of your logged sets."
                ),
                action=(
                    f"Add more work for other muscle groups before increasing "
                    f"{f['top_muscle']} volume."
                ),
                priority="medium",
                category="workout",
                impact=7,
                metric="muscle_imbalance",
            ))

        # -- Low protein --
        ppkg = f["protein_per_kg"]
        if ppkg is not None and ppkg < 1.6:
            if ppkg < 1.0:
                recs.append(AICoachRecommendation(
                    title="Increase protein intake urgently",
                    reason=(
                        f"Your average protein intake is {round(ppkg, 2)} g/kg, "
                        f"well below the minimum for strength training."
                    ),
                    action="Add two high-protein meals or snacks today.",
                    priority="high",
                    category="nutrition",
                    impact=10,
                    metric="protein_per_kg",
                ))
            elif ppkg < 1.2:
                recs.append(AICoachRecommendation(
                    title="Increase protein intake",
                    reason=(
                        f"Your average protein intake is {round(ppkg, 2)} g/kg, "
                        f"below the recommended strength-training range."
                    ),
                    action="Add one high-protein meal or snack today.",
                    priority="high",
                    category="nutrition",
                    impact=9,
                    metric="protein_per_kg",
                ))
            else:
                recs.append(AICoachRecommendation(
                    title="Increase protein intake",
                    reason=(
                        f"Your average protein intake is {round(ppkg, 2)} g/kg, "
                        f"slightly below the optimal range."
                    ),
                    action="Add one high-protein meal or snack today.",
                    priority="medium",
                    category="nutrition",
                    impact=6,
                    metric="protein_per_kg",
                ))

        # -- Missing bodyweight --
        if ppkg is None:
            recs.append(AICoachRecommendation(
                title="Add your bodyweight",
                reason="Protein-per-kg analysis needs your current bodyweight.",
                action="Update your weight in your profile to unlock more accurate nutrition insights.",
                priority="low",
                category="nutrition",
                impact=3,
                metric="bodyweight",
            ))

        # -- Poor nutrition logging --
        lcp = f["logging_consistency_percent"]
        if lcp < 70:
            logged = f["logged_days"]
            impact = 8 if lcp < 50 else 5
            recs.append(AICoachRecommendation(
                title="Improve nutrition logging consistency",
                reason=f"Nutrition was logged on only {logged} out of {self.days} days.",
                action="Log at least your main meals for the next 3 days.",
                priority="high" if impact >= 8 else "medium",
                category="nutrition",
                impact=impact,
                metric="logging_consistency",
            ))

        # -- Calorie variability --
        cv = f["calorie_cv"]
        if cv is not None and cv > 0.25:
            cv_impact = 8 if cv > 0.35 else 6
            cv_priority = "high" if cv > 0.35 else "medium"
            recs.append(AICoachRecommendation(
                title="Stabilize daily calories",
                reason="Your daily calories varied significantly across logged days.",
                action="Keep your next few days within a similar calorie range.",
                priority=cv_priority,
                category="nutrition",
                impact=cv_impact,
                metric="calorie_variability",
            ))

        # -- Poor recovery nutrition --
        if (
            vcp is not None
            and vcp > 25
            and ppkg is not None
            and ppkg < 1.6
        ):
            warns.append(AICoachWarning(
                code="recovery_support_low",
                title="Recovery support may be low",
                detail="Training volume increased while protein intake is below target.",
                priority="high",
            ))
            recs.append(AICoachRecommendation(
                title="Support recovery with protein",
                reason="Higher training load requires better nutrition support.",
                action="Add a protein-rich meal after your next workout.",
                priority="high",
                category="recovery",
                impact=9,
                metric="recovery_nutrition",
            ))

        return recs, warns

    # ── Confidence ────────────────────────────────────────────────────────

    def _estimate_confidence(self, f: dict):
        w = f["workouts_this_period"]
        ld = f["logged_days"]
        has_weight = f["has_bodyweight"]
        logging_ok = f["logging_consistency_percent"] >= 70

        if w >= 2 and ld >= max(5, int(self.days * 0.7)) and has_weight:
            return (
                "high",
                "Workout and nutrition data are both well represented for this period.",
            )

        # Count weak domains
        weak = 0
        if w < 1:
            weak += 1
        if ld < 2:
            weak += 1
        if not has_weight:
            weak += 1
        if not logging_ok:
            weak += 1

        if weak <= 1 and (w >= 1 or ld >= 2):
            return (
                "medium",
                "Some workout and nutrition data are available, but logging is incomplete.",
            )

        return (
            "low",
            "There is limited workout or nutrition data for this period.",
        )

    # ── Data-state derive ─────────────────────────────────────────────────

    @staticmethod
    def _derive_data_state(f: dict) -> DataState:
        """Return a 5-level token describing how much data was available."""
        w = f["workouts_this_period"]
        ld = f["logged_days"]
        if w == 0 and ld == 0:
            return "no_data"
        if w > 0 and ld == 0:
            return "workout_only"
        if w == 0 and ld > 0:
            return "nutrition_only"
        # Both present but sparse
        if w < 2 or ld < 3:
            return "limited"
        return "sufficient"

    # ── Summary text ──────────────────────────────────────────────────────

    @staticmethod
    def _generate_summary_text(
        data_state: DataState, f: dict, readiness_label: str
    ) -> str:
        """Return a summary grounded in the actual data state.

        State A (no_data): no workouts, no nutrition.
        State B (workout_only): workouts but no nutrition.
        State C (nutrition_only): nutrition but no workouts.
        State D (limited): both present but sparse.
        State E (sufficient): enough data for a grounded summary.
        """
        w = f["workouts_this_period"]
        ld = f["logged_days"]

        if data_state == "no_data":
            return (
                "No workouts or nutrition entries were logged during this "
                "analysis period. Log your next workout and meals to start "
                "building a reliable weekly assessment."
            )

        if data_state == "workout_only":
            return (
                f"Training activity was recorded ({w} workout"
                f"{'s' if w != 1 else ''}), but there is not enough nutrition "
                "data to evaluate your overall week reliably. Your current "
                "assessment is based mainly on workout activity."
            )

        if data_state == "nutrition_only":
            return (
                f"Nutrition information was logged on {ld} day"
                f"{'s' if ld != 1 else ''}, but no completed workouts were "
                "found during this period. Training and overall-readiness "
                "conclusions are not yet reliable."
            )

        if data_state == "limited":
            return (
                "This is an early signal based on limited workout and "
                "nutrition data. Continue logging consistently before "
                "treating the weekly scores as a stable trend."
            )

        # Sufficient data — grounded summary using actual metrics
        labels = {
            "Excellent": (
                "Your training consistency and nutrition coverage were both "
                "strong this week. Based on your recorded sessions, "
                "recovery load appears well managed."
            ),
            "Good": (
                "Your week is performing well overall, with a few areas for "
                "improvement. Based on the available data, "
                "training consistency was the strongest signal."
            ),
            "Moderate": (
                "Some key signals need attention this week. Based on your "
                "recorded sessions, focus on the highest-priority action "
                "first rather than changing everything at once."
            ),
            "Needs Attention": (
                "Your recent data shows several issues that may limit "
                "progress. Based on the available data, training volume "
                "or nutrition coverage needs work. Start with the next "
                "best action and rebuild consistency gradually."
            ),
        }
        return labels.get(readiness_label, labels["Moderate"])

    # ── Next best action ──────────────────────────────────────────────────

    @staticmethod
    def _generate_next_best_action(
        recommendations: List[AICoachRecommendation],
        data_state: DataState,
        f: dict,
    ) -> Optional[str]:
        """Return the single most important action for the user right now.

        When data is absent the action must prompt logging, not training advice.
        """
        w = f["workouts_this_period"]
        ld = f["logged_days"]

        if data_state == "no_data":
            return (
                "Log your first workout to start building your weekly assessment."
            )

        if data_state == "workout_only":
            return (
                "Log today's meals — nutrition coverage is currently limiting "
                "the reliability of your weekly insight."
            )

        if data_state == "nutrition_only":
            return (
                "Log your next completed workout — training data is missing "
                "and required for a reliable weekly assessment."
            )

        if data_state == "limited":
            if w < 2:
                return (
                    "Complete at least one more workout this week to improve "
                    "the reliability of your training score."
                )
            if ld < 3:
                return (
                    "Log meals for a few more days — nutrition coverage is "
                    "too sparse for a reliable assessment."
                )

        if not recommendations:
            return None

        # Sufficient data — pick the highest-impact action
        workout_recovery = [
            r for r in recommendations if r.category in ("workout", "recovery")
        ]
        nutrition = [r for r in recommendations if r.category == "nutrition"]

        top_wr = workout_recovery[0] if workout_recovery else None
        top_nut = nutrition[0] if nutrition else None

        if top_wr and top_nut:
            wr_action = top_wr.action.rstrip(".")
            nut_action = top_nut.action[0].lower() + top_nut.action[1:]
            nut_action = nut_action.rstrip(".")
            return f"{wr_action} and {nut_action}."
        elif top_wr:
            return top_wr.action
        elif top_nut:
            return top_nut.action
        return recommendations[0].action
