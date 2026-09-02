"""DAUNTRA Lab Insights V2 deterministic analytics.

Facts, normalization, comparisons, confidence and wording are generated from
canonical application data. Unknown values stay null; this module intentionally
contains no readiness/recovery score and no generative-AI dependency.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session, joinedload

from models.lab_insights import LabAnalysisSnapshot, LabInsightState
from models.nutrition import NutritionDayStatus, NutritionLog
from models.program import Program
from models.schedule import ScheduledWorkout
from models.user import User
from models.workout import Workout, WorkoutSet


SCHEMA_VERSION = "2.0"
ANALYTICS_VERSION = "lab-v2.0.0"
STALE_AFTER = timedelta(minutes=15)
DISCLAIMER = (
    "Lab describes patterns in the data you recorded. It does not measure "
    "physiological recovery and is not medical advice."
)

_CONFIDENCE_WEIGHT = {"Insufficient": 0.0, "Low": 0.35, "Medium": 0.7, "High": 1.0}
_BUCKET_WEIGHT = {
    "High Priority": 4.0,
    "Watch": 3.0,
    "Positive Trend": 2.5,
    "Informational": 1.5,
    "Insufficient Data": 1.0,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _exercise_key(item: WorkoutSet) -> tuple[str | None, bool]:
    stable_id = (item.exercise_id or "").strip()
    if stable_id:
        return f"id:{stable_id}", True
    name = _normalize_name(item.exercise_name or "")
    return (f"name:{name}", False) if name else (None, False)


def _valid_set(item: WorkoutSet) -> bool:
    return (
        1 <= (item.sets or 0) <= 20
        and 1 <= (item.reps or 0) <= 100
        and (item.weight_kg is None or 0 <= item.weight_kg <= 1000)
    )


def _set_workload(item: WorkoutSet) -> float | None:
    if not _valid_set(item) or item.weight_kg is None:
        return None
    return float(item.sets * item.reps * item.weight_kg)


def _estimated_strength(item: WorkoutSet) -> float | None:
    """Epley estimate only inside its defensible comparable rep range."""
    if (
        not _valid_set(item)
        or item.weight_kg is None
        or item.weight_kg <= 0
        or item.reps > 12
    ):
        return None
    return float(item.weight_kg * (1 + item.reps / 30))


def _window(start: date, end: date) -> dict[str, Any]:
    return {"start": start, "end": end, "days": (end - start).days + 1}


def _value(label: str, value: Any, unit: str | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit}


def _confidence(
    *,
    sample_count: int,
    eligible_count: int | None = None,
    excluded_count: int = 0,
    baseline_weeks: float = 0,
    coverage_percent: float | None = None,
    comparability: str | None = None,
    required: int = 2,
) -> dict[str, Any]:
    eligible = sample_count if eligible_count is None else eligible_count
    if eligible < required:
        level = "Insufficient"
        reason = f"Only {eligible} eligible observation{'s' if eligible != 1 else ''}."
    elif baseline_weeks >= 6 and eligible >= 10:
        level = "High"
        reason = f"{eligible} eligible observations across {baseline_weeks:.1f} baseline weeks."
    elif (baseline_weeks >= 3 and eligible >= 5) or eligible >= 7:
        level = "Medium"
        reason = f"{eligible} eligible observations provide a usable comparison."
    else:
        level = "Low"
        reason = f"The finding is based on {eligible} eligible observations."
    return {
        "level": level,
        "reason": reason,
        "sample_count": sample_count,
        "eligible_sample_count": eligible,
        "excluded_sample_count": excluded_count,
        "baseline_weeks": round(baseline_weeks, 1),
        "coverage_percent": _round(coverage_percent),
        "comparability": comparability,
    }


def _evidence(
    *,
    start: date,
    end: date,
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    delta: dict[str, Any] | None,
    confidence: dict[str, Any],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "period_start": start,
        "period_end": end,
        "current": current,
        "baseline": baseline,
        "delta": delta,
        "sample_count": confidence["sample_count"],
        "eligible_sample_count": confidence["eligible_sample_count"],
        "excluded_sample_count": confidence["excluded_sample_count"],
        "baseline_weeks": confidence["baseline_weeks"],
        "coverage_percent": confidence["coverage_percent"],
        "limitations": limitations or [],
    }


class LabInsightsV2Engine:
    RECENT_DAYS = 21
    MEDIUM_DAYS = 42
    BASELINE_DAYS = 56
    LONG_DAYS = 180

    def __init__(
        self,
        db: Session,
        user: User,
        *,
        now: datetime | None = None,
    ) -> None:
        self.db = db
        self.user = user
        self.now = _as_utc(now) or _utc_now()
        self.timezone_name, self.user_tz = self._resolve_timezone(user.timezone)
        self.today = self.now.astimezone(self.user_tz).date()
        self.recent_start = self.today - timedelta(days=self.RECENT_DAYS - 1)
        self.medium_start = self.today - timedelta(days=self.MEDIUM_DAYS - 1)
        self.baseline_end = self.recent_start - timedelta(days=1)
        self.baseline_start = self.baseline_end - timedelta(days=self.BASELINE_DAYS - 1)
        self.long_start = self.today - timedelta(days=self.LONG_DAYS - 1)

    @staticmethod
    def _resolve_timezone(value: str | None) -> tuple[str, ZoneInfo]:
        candidate = (value or "UTC").strip() or "UTC"
        try:
            return candidate, ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            return "UTC", ZoneInfo("UTC")

    def generate(self, *, force_refresh: bool = False) -> dict[str, Any]:
        source = self._load_source()
        watermark = self._watermark(source)
        if not force_refresh:
            cached = self._cached_snapshot(watermark)
            if cached is not None:
                payload = deepcopy(cached.payload)
                payload["cache_status"] = "hit"
                payload["is_stale"] = self.now >= (_as_utc(cached.stale_after) or self.now)
                return payload

        payload = self._calculate(source, watermark)
        encoded = jsonable_encoder(payload)
        existing = (
            self.db.query(LabAnalysisSnapshot)
            .filter(
                LabAnalysisSnapshot.user_id == self.user.id,
                LabAnalysisSnapshot.analytics_version == ANALYTICS_VERSION,
                LabAnalysisSnapshot.source_data_watermark == watermark,
            )
            .first()
        )
        if existing is None:
            existing = LabAnalysisSnapshot(
                analysis_id=payload["analysis_id"],
                user_id=self.user.id,
                analytics_version=ANALYTICS_VERSION,
                source_data_watermark=watermark,
                generated_at=self.now,
                data_through=payload["data_through"],
                stale_after=payload["stale_after"],
                payload=encoded,
            )
            self.db.add(existing)
        else:
            existing.analysis_id = payload["analysis_id"]
            existing.generated_at = self.now
            existing.data_through = payload["data_through"]
            existing.stale_after = payload["stale_after"]
            existing.payload = encoded
        self.db.commit()
        self._prune_snapshots()
        return payload

    def stale_fallback(self) -> dict[str, Any] | None:
        snapshot = (
            self.db.query(LabAnalysisSnapshot)
            .filter(LabAnalysisSnapshot.user_id == self.user.id)
            .order_by(LabAnalysisSnapshot.generated_at.desc())
            .first()
        )
        if snapshot is None:
            return None
        payload = deepcopy(snapshot.payload)
        payload["is_stale"] = True
        payload["cache_status"] = "stale_fallback"
        return payload

    def record_impressions(self, insight_ids: list[str]) -> int:
        if not insight_ids:
            return 0
        states = self.db.query(LabInsightState).filter(
            LabInsightState.user_id == self.user.id
        ).all()
        wanted = set(insight_ids)
        changed = 0
        for state in states:
            if state.last_payload.get("id") in wanted:
                state.last_shown_at = self.now
                changed += 1
        self.db.commit()
        return changed

    def _cached_snapshot(self, watermark: str) -> LabAnalysisSnapshot | None:
        return (
            self.db.query(LabAnalysisSnapshot)
            .filter(
                LabAnalysisSnapshot.user_id == self.user.id,
                LabAnalysisSnapshot.analytics_version == ANALYTICS_VERSION,
                LabAnalysisSnapshot.source_data_watermark == watermark,
                LabAnalysisSnapshot.stale_after > self.now,
            )
            .first()
        )

    def _load_source(self) -> dict[str, Any]:
        workouts = (
            self.db.query(Workout)
            .options(joinedload(Workout.sets))
            .filter(
                Workout.user_id == self.user.id,
                Workout.completed_at.is_not(None),
                Workout.date >= self.long_start,
                Workout.date <= self.today,
            )
            .order_by(Workout.date, Workout.id)
            .all()
        )
        nutrition = (
            self.db.query(NutritionLog)
            .filter(
                NutritionLog.user_id == self.user.id,
                NutritionLog.date >= self.medium_start,
                NutritionLog.date <= self.today,
            )
            .order_by(NutritionLog.date, NutritionLog.id)
            .all()
        )
        day_statuses = (
            self.db.query(NutritionDayStatus)
            .filter(
                NutritionDayStatus.user_id == self.user.id,
                NutritionDayStatus.date >= self.medium_start,
                NutritionDayStatus.date <= self.today,
            )
            .order_by(NutritionDayStatus.date)
            .all()
        )
        schedules = (
            self.db.query(ScheduledWorkout)
            .filter(
                ScheduledWorkout.user_id == self.user.id,
                ScheduledWorkout.scheduled_date >= self.medium_start,
                ScheduledWorkout.scheduled_date <= self.today,
            )
            .order_by(ScheduledWorkout.scheduled_date, ScheduledWorkout.id)
            .all()
        )
        programs = self.db.query(Program).filter(Program.user_id == self.user.id).all()
        return {
            "workouts": workouts,
            "nutrition": nutrition,
            "day_statuses": day_statuses,
            "schedules": schedules,
            "programs": programs,
        }

    def _watermark(self, source: dict[str, Any]) -> str:
        canonical = {
            "context": [
                self.timezone_name,
                self.user.canonical_goal,
                self.user.weight_kg,
                self.user.calorie_target,
                self.user.protein_target_g,
                self.user.carbs_target_g,
                self.user.fat_target_g,
            ],
            "workouts": [
                [
                    w.id,
                    _iso(w.date),
                    _iso(w.completed_at),
                    w.completion_inferred,
                    w.program_id,
                    w.program_day_id,
                    w.scheduled_workout_id,
                    [
                        [s.id, s.exercise_id, s.exercise_name, s.sets, s.reps, s.weight_kg]
                        for s in sorted(w.sets, key=lambda row: row.id)
                    ],
                ]
                for w in source["workouts"]
            ],
            "nutrition": [
                [n.id, _iso(n.date), n.calories, n.protein_g, n.carbs_g, n.fat_g]
                for n in source["nutrition"]
            ],
            "completion": [
                [s.id, _iso(s.date), s.is_complete, _iso(s.completed_at), _iso(s.updated_at)]
                for s in source["day_statuses"]
            ],
            "schedules": [
                [
                    s.id,
                    s.program_id,
                    s.program_day_id,
                    _iso(s.scheduled_date),
                    s.status,
                    s.linkage_trustworthy,
                    _iso(s.completed_at),
                ]
                for s in source["schedules"]
            ],
            "programs": [
                [p.id, p.is_active, _iso(p.activated_at), p.days_per_week]
                for p in source["programs"]
            ],
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _calculate(self, source: dict[str, Any], watermark: str) -> dict[str, Any]:
        workouts: list[Workout] = source["workouts"]
        recent = [w for w in workouts if self.recent_start <= w.date <= self.today]
        baseline = [w for w in workouts if self.baseline_start <= w.date <= self.baseline_end]
        medium = [w for w in workouts if self.medium_start <= w.date <= self.today]

        limitations: list[str] = []
        if self.user.timezone is None:
            limitations.append("User timezone is not configured; UTC boundaries were used.")
        elif self.timezone_name != self.user.timezone:
            limitations.append("Configured timezone was invalid; UTC boundaries were used.")
        if self.user.weight_kg is None:
            limitations.append(
                "Bodyweight is unavailable; weight-relative findings are suppressed."
            )

        frequency_metric, frequency_candidates = self._training_frequency(
            workouts, recent, baseline
        )
        program_metric, program_candidates = self._program_execution(
            source["schedules"], recent
        )
        workload_metric, workload_candidates = self._workload(recent, baseline)
        performance_metric, performance_candidates = self._performance(workouts, medium)
        nutrition_metric, nutrition_candidates = self._nutrition(
            source["nutrition"], source["day_statuses"]
        )

        candidates = (
            frequency_candidates
            + program_candidates
            + performance_candidates
            + workload_candidates
            + nutrition_candidates
        )
        active, resolved = self._apply_lifecycle(candidates)
        selected = self._prioritize(active)
        domain_coverage = self._domain_coverage(
            frequency_metric,
            program_metric,
            performance_metric,
            workload_metric,
            nutrition_metric,
        )
        if not recent:
            limitations.append("No completed workouts exist in the recent 21-day window.")
        if nutrition_metric["complete_days"] == 0:
            limitations.append(
                "No intake days were explicitly complete; nutrition adequacy was not assessed."
            )
        if program_metric["eligible_opportunities"] is None:
            limitations.append(
                "Plan comparison is unavailable without trustworthy scheduled-workout linkage."
            )

        data_through = self._data_through(source)
        return {
            "schema_version": SCHEMA_VERSION,
            "analytics_version": ANALYTICS_VERSION,
            "analysis_id": str(uuid4()),
            "generated_at": self.now,
            "data_through": data_through,
            "stale_after": self.now + STALE_AFTER,
            "is_stale": False,
            "cache_status": "generated",
            "source_data_watermark": watermark,
            "user_timezone": self.timezone_name,
            "period": {
                "recent": _window(self.recent_start, self.today),
                "medium_term": _window(self.medium_start, self.today),
                "personal_baseline": _window(self.baseline_start, self.baseline_end),
            },
            "domain_coverage": domain_coverage,
            "metrics": {
                "training_frequency": frequency_metric,
                "program_execution": program_metric,
                "performance": performance_metric,
                "workload": workload_metric,
                "nutrition": nutrition_metric,
            },
            "insights": selected,
            "resolved_insights": resolved[:5],
            "limitations": list(dict.fromkeys(limitations)),
            "disclaimer": DISCLAIMER,
        }

    def _training_frequency(
        self,
        all_workouts: list[Workout],
        recent: list[Workout],
        baseline: list[Workout],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current_rate = len(recent) / (self.RECENT_DAYS / 7)
        baseline_rate = len(baseline) / (self.BASELINE_DAYS / 7) if baseline else None
        delta = current_rate - baseline_rate if baseline_rate is not None else None
        baseline_weeks = self.BASELINE_DAYS / 7 if baseline else 0
        conf = _confidence(
            sample_count=len(recent) + len(baseline),
            eligible_count=len(recent) + len(baseline),
            baseline_weeks=baseline_weeks,
            required=4,
        )
        metric = {
            "recent_sessions": len(recent),
            "recent_sessions_per_week": _round(current_rate),
            "baseline_sessions": len(baseline),
            "baseline_sessions_per_week": _round(baseline_rate),
            "absolute_delta_sessions_per_week": _round(delta),
            "confidence": conf,
        }
        evidence = _evidence(
            start=self.recent_start,
            end=self.today,
            current=_value("Recent", _round(current_rate), "sessions/week"),
            baseline=(
                _value("Personal baseline", _round(baseline_rate), "sessions/week")
                if baseline_rate is not None
                else None
            ),
            delta=(
                _value("Change", _round(delta), "sessions/week") if delta is not None else None
            ),
            confidence=conf,
            limitations=[] if baseline else ["No comparable personal baseline is available."],
        )
        candidates: list[dict[str, Any]] = []
        older = [w for w in all_workouts if w.date < self.recent_start]
        first_recent = min((w.date for w in recent), default=None)
        prior_gap = (
            (first_recent - max(w.date for w in older)).days
            if first_recent is not None and older
            else None
        )
        if recent and prior_gap is not None and prior_gap >= 28:
            candidates.append(
                self._candidate(
                    "training_frequency", 1, "overall", "Training Rhythm",
                    "Positive Trend", "Training rhythm resumed",
                    f"You completed {len(recent)} sessions after a {prior_gap}-day gap in recorded training.",
                    "Keep the next session achievable and build rhythm from recorded completions.",
                    conf, evidence, magnitude=min(prior_gap / 28, 3), direction="positive",
                )
            )
        elif baseline_rate is None:
            candidates.append(
                self._candidate(
                    "training_frequency_baseline", 1, "overall", "Training Rhythm",
                    "Insufficient Data", "A personal training baseline is still forming",
                    f"{len(recent)} completed sessions are available in the recent window.",
                    "Complete workouts normally; Lab will compare rhythm once enough history exists.",
                    conf, evidence, magnitude=0.2, direction="neutral",
                )
            )
        elif delta is not None and delta >= 0.75 and current_rate >= baseline_rate * 1.25:
            candidates.append(
                self._candidate(
                    "training_frequency", 1, "overall", "Training Rhythm",
                    "Positive Trend", "Training frequency increased",
                    f"Recent frequency is {_round(delta)} sessions/week above your personal baseline.",
                    "Check that the higher rhythm still fits your current program and schedule.",
                    conf, evidence, magnitude=abs(delta), direction="positive",
                )
            )
        elif delta is not None and delta <= -0.75 and current_rate <= baseline_rate * 0.7:
            candidates.append(
                self._candidate(
                    "training_frequency", 1, "overall", "Training Rhythm",
                    "Watch", "Training frequency declined",
                    f"Recent frequency is {abs(_round(delta) or 0)} sessions/week below your personal baseline.",
                    "Choose one realistic next session to re-establish your usual rhythm.",
                    conf, evidence, magnitude=abs(delta), direction="negative", severity=abs(delta),
                )
            )
        elif len(recent) >= 2 and abs(delta or 0) < 0.75:
            candidates.append(
                self._candidate(
                    "training_frequency", 1, "overall", "Training Rhythm",
                    "Positive Trend", "Training rhythm is stable",
                    "Recent completed-session frequency is close to your personal baseline.",
                    None, conf, evidence, magnitude=0.5, direction="positive",
                )
            )
        return metric, candidates

    def _program_execution(
        self, schedules: list[ScheduledWorkout], recent: list[Workout]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        recent_schedules = [
            item for item in schedules if self.recent_start <= item.scheduled_date <= self.today
        ]
        eligible = [
            item
            for item in recent_schedules
            if item.linkage_trustworthy and item.status in {"planned", "completed"}
        ]
        linked_ids = {w.scheduled_workout_id for w in recent if w.scheduled_workout_id}
        matched = [
            item for item in eligible if item.id in linked_ids or item.status == "completed"
        ]
        available = bool(eligible)
        percent = len(matched) / len(eligible) * 100 if available else None
        conf = _confidence(
            sample_count=len(recent_schedules),
            eligible_count=len(eligible),
            excluded_count=len(recent_schedules) - len(eligible),
            baseline_weeks=0,
            coverage_percent=percent,
            comparability="exact schedule linkage" if available else None,
            required=2,
        )
        metric = {
            "matched_completed_opportunities": len(matched) if available else None,
            "eligible_opportunities": len(eligible) if available else None,
            "execution_percent": _round(percent),
            "excluded_untrusted_or_cancelled": len(recent_schedules) - len(eligible),
            "confidence": conf,
        }
        ev = _evidence(
            start=self.recent_start,
            end=self.today,
            current=(
                _value("Completed planned opportunities", len(matched), "sessions")
                if available else None
            ),
            baseline=(
                _value("Eligible planned opportunities", len(eligible), "sessions")
                if available else None
            ),
            delta=_value("Program execution", _round(percent), "%") if available else None,
            confidence=conf,
            limitations=([] if available else ["No trustworthy planned opportunities are linked."]),
        )
        candidates: list[dict[str, Any]] = []
        if not available:
            candidates.append(
                self._candidate(
                    "program_execution_data", 1, "overall", "Program Execution",
                    "Insufficient Data", "Plan comparison unavailable",
                    "Lab cannot match completed workouts to eligible planned opportunities yet.",
                    "Start workouts from a scheduled program day to establish exact linkage.",
                    conf, ev, magnitude=0.3, direction="neutral",
                )
            )
        elif len(eligible) >= 2 and percent is not None and percent >= 80:
            candidates.append(
                self._candidate(
                    "program_execution", 1, "overall", "Program Execution",
                    "Positive Trend", "Planned sessions were executed consistently",
                    f"{len(matched)} of {len(eligible)} eligible planned opportunities were completed.",
                    None, conf, ev, magnitude=percent / 100, direction="positive",
                )
            )
        elif len(eligible) >= 2 and percent is not None and percent < 60:
            candidates.append(
                self._candidate(
                    "program_execution", 1, "overall", "Program Execution",
                    "Watch", "Program execution is below recent opportunities",
                    f"{len(matched)} of {len(eligible)} eligible planned opportunities were completed.",
                    "Review the next scheduled day and reschedule it if the date is no longer realistic.",
                    conf, ev, magnitude=(100 - percent) / 100, direction="negative",
                    severity=(100 - percent) / 100,
                )
            )
        return metric, candidates

    def _workload(
        self, recent: list[Workout], baseline: list[Workout]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current_map, current_total, current_excluded, current_names = self._workload_by_key(recent)
        baseline_map, baseline_total, baseline_excluded, baseline_names = self._workload_by_key(baseline)
        common = set(current_map) & set(baseline_map)
        comparable_current = sum(current_map[key] for key in common)
        comparable_baseline = sum(baseline_map[key] for key in common)
        comparability = comparable_current / current_total if current_total > 0 else 0.0
        delta_percent = None
        if comparable_baseline > 0 and comparability >= 0.5:
            delta_percent = (comparable_current - comparable_baseline) / comparable_baseline * 100
        stable_ids = all(not key.startswith("name:") for key in common) if common else False
        conf = _confidence(
            sample_count=len(recent) + len(baseline),
            eligible_count=len(common),
            excluded_count=current_excluded + baseline_excluded,
            baseline_weeks=self.BASELINE_DAYS / 7 if baseline else 0,
            coverage_percent=comparability * 100 if current_total else None,
            comparability=(
                "stable exercise IDs" if stable_ids else "normalized legacy exercise names"
            ) if common else None,
            required=2,
        )
        metric = {
            "recent_comparable_workload_kg": _round(comparable_current) if common else None,
            "baseline_comparable_workload_kg": _round(comparable_baseline) if common else None,
            "change_percent": _round(delta_percent),
            "comparable_exercise_count": len(common),
            "exercise_mix_coverage_percent": _round(comparability * 100) if current_total else None,
            "excluded_set_count": current_excluded + baseline_excluded,
            "confidence": conf,
        }
        ev = _evidence(
            start=self.recent_start,
            end=self.today,
            current=(
                _value("Recent comparable workload", _round(comparable_current), "kg")
                if common else None
            ),
            baseline=(
                _value("Baseline comparable workload", _round(comparable_baseline), "kg")
                if comparable_baseline > 0 else None
            ),
            delta=(
                _value("Change", _round(delta_percent), "%") if delta_percent is not None else None
            ),
            confidence=conf,
            limitations=(
                [] if delta_percent is not None else ["No sufficiently comparable prior workload baseline exists."]
            ),
        )
        candidates: list[dict[str, Any]] = []
        if recent and delta_percent is None:
            candidates.append(
                self._candidate(
                    "workload_baseline", 1, "comparable", "Workload",
                    "Insufficient Data", "Workload comparison unavailable",
                    "Recent workload exists, but there is no sufficiently comparable prior baseline.",
                    None, conf, ev, magnitude=0.2, direction="neutral",
                )
            )
        elif delta_percent is not None and abs(delta_percent) >= 25:
            direction = "increased" if delta_percent > 0 else "decreased"
            candidates.append(
                self._candidate(
                    "comparable_workload_change", 1, "overall", "Workload", "Watch",
                    f"Comparable workload {direction}",
                    f"Workload across {len(common)} comparable exercises changed by {abs(_round(delta_percent) or 0)}%.",
                    "Review the exercise-level evidence alongside your current program before changing the plan.",
                    conf, ev, magnitude=min(abs(delta_percent) / 100, 3),
                    direction="neutral", severity=abs(delta_percent) / 100,
                )
            )
        return metric, candidates

    @staticmethod
    def _workload_by_key(
        workouts: list[Workout],
    ) -> tuple[dict[str, float], float, int, dict[str, str]]:
        values: dict[str, float] = defaultdict(float)
        names: dict[str, str] = {}
        total = 0.0
        excluded = 0
        for workout in workouts:
            for item in workout.sets:
                key, _ = _exercise_key(item)
                workload = _set_workload(item)
                if key is None or workload is None:
                    excluded += 1
                    continue
                values[key] += workload
                names[key] = item.exercise_name
                total += workload
        return dict(values), total, excluded, names

    def _performance(
        self, all_workouts: list[Workout], medium: list[Workout]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        excluded = 0
        for workout in all_workouts:
            by_key: dict[str, list[WorkoutSet]] = defaultdict(list)
            for item in workout.sets:
                key, stable = _exercise_key(item)
                if key is None or not _valid_set(item):
                    excluded += 1
                    continue
                by_key[key].append(item)
            for key, items in by_key.items():
                strengths = [v for v in (_estimated_strength(item) for item in items) if v is not None]
                weights = [float(item.weight_kg) for item in items if item.weight_kg is not None]
                reps = [item.reps for item in items]
                observations[key].append(
                    {
                        "date": workout.date,
                        "name": items[0].exercise_name,
                        "stable": not key.startswith("name:"),
                        "strength": max(strengths) if strengths else None,
                        "load": max(weights) if weights else None,
                        "reps": max(reps) if reps else None,
                    }
                )

        candidates: list[dict[str, Any]] = []
        progressed = 0
        stable_count = 0
        declined = 0
        pr_count = 0
        for key, rows in observations.items():
            rows.sort(key=lambda item: item["date"])
            current = [row for row in rows if self.recent_start <= row["date"] <= self.today]
            prior = [row for row in rows if row["date"] < self.recent_start]
            if not current:
                continue
            name = current[-1]["name"]
            stable_identity = all(row["stable"] for row in rows)
            limitations = [] if stable_identity else ["Legacy exercise-name matching lowers identity confidence."]

            current_loads = [row["load"] for row in current if row["load"] is not None]
            prior_loads = [row["load"] for row in prior if row["load"] is not None]
            if len(prior_loads) >= 2 and current_loads:
                current_max = max(current_loads)
                prior_max = max(prior_loads)
                if current_max > prior_max + max(0.5, prior_max * 0.01):
                    conf = _confidence(
                        sample_count=len(rows), eligible_count=len(prior_loads) + len(current_loads),
                        baseline_weeks=self._history_weeks(rows),
                        comparability="stable exercise ID" if stable_identity else "normalized exercise name",
                        required=3,
                    )
                    ev = _evidence(
                        start=self.recent_start, end=self.today,
                        current=_value("New heaviest load", _round(current_max), "kg"),
                        baseline=_value("Previous heaviest load", _round(prior_max), "kg"),
                        delta=_value("Increase", _round(current_max - prior_max), "kg"),
                        confidence=conf, limitations=limitations,
                    )
                    candidates.append(
                        self._candidate(
                            "personal_record", 1, key, "Performance", "Positive Trend",
                            f"New {name} load record",
                            f"Your recorded load reached {_round(current_max)} kg, above the prior {_round(prior_max)} kg best.",
                            None, conf, ev,
                            magnitude=1 + (current_max - prior_max) / max(prior_max, 1),
                            direction="positive",
                        )
                    )
                    pr_count += 1

            current_strength = [row["strength"] for row in current if row["strength"] is not None]
            baseline_strength = [
                row["strength"]
                for row in prior
                if self.baseline_start <= row["date"] <= self.baseline_end
                and row["strength"] is not None
            ]
            if len(current_strength) >= 2 and len(baseline_strength) >= 3:
                current_mean = sum(current_strength) / len(current_strength)
                baseline_mean = sum(baseline_strength) / len(baseline_strength)
                change = (current_mean - baseline_mean) / baseline_mean * 100 if baseline_mean > 0 else None
                conf = _confidence(
                    sample_count=len(rows),
                    eligible_count=len(current_strength) + len(baseline_strength),
                    baseline_weeks=self._history_weeks(rows),
                    comparability="stable exercise ID" if stable_identity else "normalized exercise name",
                    required=5,
                )
                ev = _evidence(
                    start=self.recent_start, end=self.today,
                    current=_value("Recent estimated strength", _round(current_mean), "kg"),
                    baseline=_value("Personal baseline", _round(baseline_mean), "kg"),
                    delta=_value("Change", _round(change), "%") if change is not None else None,
                    confidence=conf,
                    limitations=limitations + ["Estimated strength uses comparable sets of 12 reps or fewer."],
                )
                if change is not None and change >= 3:
                    progressed += 1
                    candidates.append(
                        self._candidate(
                            "exercise_progression", 1, key, "Performance", "Positive Trend",
                            f"{name} performance progressed",
                            f"Recent estimated strength is {_round(change)}% above your personal baseline.",
                            "Keep logging the exercise ID, load and reps so the comparison remains stable.",
                            conf, ev, magnitude=0.75 + change / 100, direction="positive",
                        )
                    )
                elif change is not None and change <= -5:
                    declined += 1
                    candidates.append(
                        self._candidate(
                            "exercise_progression", 1, key, "Performance", "Watch",
                            f"{name} performance declined",
                            f"Recent estimated strength is {abs(_round(change) or 0)}% below your personal baseline.",
                            "Review comparable sessions and your current program before changing the exercise.",
                            conf, ev, magnitude=abs(change) / 100, direction="negative",
                            severity=abs(change) / 100,
                        )
                    )

            medium_rows = [
                row for row in rows
                if self.medium_start <= row["date"] <= self.today and row["strength"] is not None
            ]
            if len(medium_rows) >= 4 and (medium_rows[-1]["date"] - medium_rows[0]["date"]).days >= 28:
                values = [row["strength"] for row in medium_rows]
                mean = sum(values) / len(values)
                spread = (max(values) - min(values)) / mean if mean > 0 else None
                if spread is not None and spread <= 0.03:
                    stable_count += 1
                    conf = _confidence(
                        sample_count=len(rows), eligible_count=len(medium_rows),
                        baseline_weeks=self._history_weeks(medium_rows),
                        comparability="stable exercise ID" if stable_identity else "normalized exercise name",
                        required=4,
                    )
                    ev = _evidence(
                        start=medium_rows[0]["date"], end=medium_rows[-1]["date"],
                        current=_value("Comparable sessions", len(medium_rows), "sessions"),
                        baseline=_value("Observed spread", _round(spread * 100), "%"),
                        delta=None, confidence=conf,
                        limitations=limitations + ["This describes stability; it does not diagnose a cause."],
                    )
                    candidates.append(
                        self._candidate(
                            "plateau_candidate", 1, key, "Performance", "Watch",
                            f"{name} performance has been stable",
                            f"Performance has been stable across {len(medium_rows)} comparable sessions.",
                            "If progression is your goal, review the exercise prescription in your program.",
                            conf, ev, magnitude=0.6, direction="neutral", severity=0.4,
                        )
                    )

        metric = {
            "exercises_observed": len(observations),
            "progressing_exercises": progressed,
            "stable_exercises": stable_count,
            "declining_exercises": declined,
            "personal_records": pr_count,
            "excluded_set_count": excluded,
        }
        return metric, candidates

    @staticmethod
    def _history_weeks(rows: list[dict[str, Any]]) -> float:
        if len(rows) < 2:
            return 0
        return max(0, (rows[-1]["date"] - rows[0]["date"]).days / 7)

    def _nutrition(
        self, logs: list[NutritionLog], statuses: list[NutritionDayStatus]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        by_date: dict[date, list[NutritionLog]] = defaultdict(list)
        for log in logs:
            by_date[log.date].append(log)
        complete_assertions = {item.date for item in statuses if item.is_complete}
        complete_dates = sorted(date_value for date_value in complete_assertions if by_date.get(date_value))
        recent_any = sorted(d for d in by_date if self.recent_start <= d <= self.today)
        recent_complete = [d for d in complete_dates if self.recent_start <= d <= self.today]
        prior_complete = [d for d in complete_dates if self.medium_start <= d < self.recent_start]
        complete_calories = {
            d: sum(float(item.calories) for item in by_date[d]) for d in recent_complete
        }
        protein_days = {
            d: sum(float(item.protein_g) for item in by_date[d])
            for d in recent_complete
            if all(item.protein_g is not None for item in by_date[d])
        }
        coverage = len(recent_complete) / self.RECENT_DAYS * 100
        any_coverage = len(recent_any) / self.RECENT_DAYS * 100
        calorie_target = self.user.calorie_target
        protein_target = self.user.protein_target_g
        calorie_met = (
            sum(value >= calorie_target for value in complete_calories.values())
            if calorie_target is not None else None
        )
        protein_met = (
            sum(value >= protein_target for value in protein_days.values())
            if protein_target is not None else None
        )
        metric = {
            "days_with_any_logs": len(recent_any),
            "complete_days": len(recent_complete),
            "logging_day_coverage_percent": _round(any_coverage),
            "complete_day_coverage_percent": _round(coverage),
            "average_complete_day_calories": (
                _round(sum(complete_calories.values()) / len(complete_calories))
                if complete_calories else None
            ),
            "average_complete_day_protein_g": (
                _round(sum(protein_days.values()) / len(protein_days)) if protein_days else None
            ),
            "calorie_target": calorie_target,
            "protein_target_g": protein_target,
            "calorie_target_days_met": calorie_met,
            "calorie_target_eligible_days": len(complete_calories) if calorie_target is not None else None,
            "protein_target_days_met": protein_met,
            "protein_target_eligible_days": len(protein_days) if protein_target is not None else None,
        }
        conf = _confidence(
            sample_count=len(recent_any), eligible_count=len(recent_complete),
            coverage_percent=coverage, required=3,
        )
        ev = _evidence(
            start=self.recent_start, end=self.today,
            current=_value("Explicitly complete days", len(recent_complete), "days"),
            baseline=_value("Days with any logs", len(recent_any), "days"),
            delta=_value("Complete-day coverage", _round(coverage), "%"),
            confidence=conf,
            limitations=([] if recent_complete else ["Intake conclusions require explicitly complete days."]),
        )
        candidates: list[dict[str, Any]] = []
        if len(recent_complete) < 3:
            candidates.append(
                self._candidate(
                    "nutrition_complete_day_coverage", 1, "overall", "Nutrition Coverage",
                    "Insufficient Data", "Nutrition analysis is limited",
                    f"Only {len(recent_complete)} day{'s were' if len(recent_complete) != 1 else ' was'} complete enough for intake analysis.",
                    "Mark a day complete only after all intake for that day has been logged.",
                    conf, ev, magnitude=0.4, direction="neutral",
                )
            )
        else:
            recent_rate = len(recent_complete) / 21
            prior_rate = len(prior_complete) / 21
            change_points = (recent_rate - prior_rate) * 100
            if len(prior_complete) >= 1 and change_points >= 15:
                candidates.append(
                    self._candidate(
                        "nutrition_coverage_trend", 1, "overall", "Nutrition Coverage",
                        "Positive Trend", "Complete-day nutrition coverage improved",
                        f"Complete-day coverage increased by {_round(change_points)} percentage points.",
                        None, conf, ev, magnitude=change_points / 100, direction="positive",
                    )
                )

        for target_name, target_value, values, met, unit in (
            ("calorie", calorie_target, complete_calories, calorie_met, "kcal"),
            ("protein", protein_target, protein_days, protein_met, "g"),
        ):
            if target_value is None or met is None or len(values) < 3:
                continue
            adherence = met / len(values) * 100
            target_conf = _confidence(
                sample_count=len(recent_any), eligible_count=len(values),
                coverage_percent=coverage, required=3,
            )
            target_ev = _evidence(
                start=self.recent_start, end=self.today,
                current=_value("Complete days meeting target", met, "days"),
                baseline=_value(f"Configured {target_name} target", _round(target_value), unit),
                delta=_value("Target adherence", _round(adherence), "%"),
                confidence=target_conf,
                limitations=["Only explicitly complete days with recorded values are eligible."],
            )
            bucket = "Positive Trend" if adherence >= 80 else "Informational"
            candidates.append(
                self._candidate(
                    f"nutrition_{target_name}_target", 1, "overall", "Nutrition Targets",
                    bucket, f"Recorded {target_name} target adherence",
                    f"Recorded {target_name} met your configured target on {met} of {len(values)} complete days.",
                    None, target_conf, target_ev, magnitude=adherence / 100,
                    direction="positive" if adherence >= 80 else "neutral",
                )
            )
        return metric, candidates

    def _candidate(
        self,
        detector_id: str,
        detector_version: int,
        subject_key: str,
        domain: str,
        bucket: str,
        title: str,
        observation: str,
        action: str | None,
        confidence: dict[str, Any],
        evidence: dict[str, Any],
        *,
        magnitude: float,
        direction: str,
        severity: float = 0,
    ) -> dict[str, Any]:
        public_id = f"{detector_id}:v{detector_version}:{hashlib.sha1(subject_key.encode()).hexdigest()[:12]}"
        priority = (
            _BUCKET_WEIGHT[bucket]
            + _CONFIDENCE_WEIGHT[confidence["level"]] * 2
            + min(max(magnitude, 0), 3)
        )
        # A genuine record or exercise-specific progression is more specific
        # and actionable than a generic stable-rhythm positive.
        if detector_id == "personal_record":
            priority += 1.5
        elif detector_id == "exercise_progression" and direction == "positive":
            priority += 0.75
        if self.user.canonical_goal and self.user.canonical_goal.lower() in {
            domain.lower(), detector_id.lower()
        }:
            priority += 0.5
        return {
            "id": public_id,
            "detector_id": detector_id,
            "detector_version": detector_version,
            "subject_key": subject_key,
            "domain": domain,
            "bucket": bucket,
            "lifecycle": "New",
            "title": title,
            "observation": observation,
            "action": action,
            "confidence": confidence,
            "evidence": evidence,
            "priority_score": round(priority, 2),
            "_direction": direction,
            "_severity": severity,
        }

    def _apply_lifecycle(
        self, candidates: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        states = {
            (state.detector_id, state.detector_version, state.subject_key): state
            for state in self.db.query(LabInsightState)
            .filter(LabInsightState.user_id == self.user.id)
            .all()
        }
        seen: set[tuple[str, int, str]] = set()
        active: list[dict[str, Any]] = []
        for candidate in candidates:
            key = (
                candidate["detector_id"],
                candidate["detector_version"],
                candidate["subject_key"],
            )
            seen.add(key)
            fingerprint_payload = {
                "bucket": candidate["bucket"],
                "title": candidate["title"],
                "evidence": candidate["evidence"],
            }
            fingerprint = hashlib.sha256(
                json.dumps(jsonable_encoder(fingerprint_payload), sort_keys=True).encode()
            ).hexdigest()
            state = states.get(key)
            if state is None:
                lifecycle = "New"
                state = LabInsightState(
                    user_id=self.user.id,
                    detector_id=key[0], detector_version=key[1], subject_key=key[2],
                    first_seen_at=self.now, last_seen_at=self.now,
                    occurrence_count=1, evidence_fingerprint=fingerprint,
                    last_payload={},
                )
                self.db.add(state)
            elif state.resolved_at is not None:
                lifecycle = "Reopened"
                state.resolved_at = None
                state.occurrence_count += 1
            else:
                previous_severity = float(state.last_payload.get("_severity", 0) or 0)
                current_severity = float(candidate.get("_severity", 0) or 0)
                lifecycle = (
                    "Improving"
                    if previous_severity > 0 and current_severity < previous_severity * 0.8
                    else "Ongoing"
                )
                state.occurrence_count += 1
            candidate["lifecycle"] = lifecycle
            if state.last_shown_at is None:
                candidate["priority_score"] += 0.5
            elif self.now - (_as_utc(state.last_shown_at) or self.now) < timedelta(days=3):
                candidate["priority_score"] = max(0, candidate["priority_score"] - 1.25)
            state.last_seen_at = self.now
            state.evidence_fingerprint = fingerprint
            state.last_payload = jsonable_encoder(candidate)
            active.append(candidate)

        resolved: list[dict[str, Any]] = []
        for key, state in states.items():
            if key in seen or state.resolved_at is not None:
                continue
            state.resolved_at = self.now
            previous = deepcopy(state.last_payload)
            if not previous:
                continue
            previous["lifecycle"] = "Resolved"
            previous["bucket"] = "Informational"
            previous["observation"] = (
                "This pattern is no longer present in the current analysis window."
            )
            previous["action"] = None
            resolved.append(previous)
        self.db.flush()
        return [self._public(item) for item in active], [self._public(item) for item in resolved]

    @staticmethod
    def _public(candidate: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in candidate.items() if not key.startswith("_")}

    @staticmethod
    def _prioritize(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(candidates, key=lambda item: item["priority_score"], reverse=True)
        chosen: list[dict[str, Any]] = []

        primary = next((item for item in ordered if item["bucket"] == "High Priority"), None)
        if primary is None:
            primary = next((item for item in ordered if item["bucket"] == "Watch"), None)
        if primary is not None:
            chosen.append(primary)

        positives = [item for item in ordered if item["bucket"] == "Positive Trend"]
        if positives and positives[0] not in chosen:
            chosen.append(positives[0])

        for item in ordered:
            if item in chosen or item["bucket"] not in {"Watch", "Informational"}:
                continue
            if sum(entry["bucket"] == "Watch" for entry in chosen) >= 2:
                continue
            chosen.append(item)
            if len(chosen) >= 4:
                break

        data_quality = next(
            (item for item in ordered if item["bucket"] == "Insufficient Data"), None
        )
        if data_quality is not None and data_quality not in chosen and len(chosen) < 5:
            chosen.append(data_quality)
        if not chosen and ordered:
            chosen.append(ordered[0])
        return chosen[:5]

    @staticmethod
    def _coverage_entry(
        domain: str,
        status: str,
        summary: str,
        sample: int,
        eligible: int,
        coverage: float | None,
        confidence: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "domain": domain,
            "status": status,
            "summary": summary,
            "sample_count": sample,
            "eligible_sample_count": eligible,
            "coverage_percent": _round(coverage),
            "confidence": confidence["level"],
        }

    def _domain_coverage(
        self,
        frequency: dict[str, Any],
        program: dict[str, Any],
        performance: dict[str, Any],
        workload: dict[str, Any],
        nutrition: dict[str, Any],
    ) -> list[dict[str, Any]]:
        session_total = frequency["recent_sessions"] + frequency["baseline_sessions"]
        program_available = program["eligible_opportunities"] is not None
        performance_available = performance["exercises_observed"] > 0
        workload_available = workload["change_percent"] is not None
        complete_days = nutrition["complete_days"]
        target_configured = any(
            nutrition[key] is not None for key in ("calorie_target", "protein_target_g")
        )
        return [
            self._coverage_entry(
                "Training Rhythm", "Available" if session_total >= 4 else "Limited",
                f"{frequency['recent_sessions']} recent completed sessions; {frequency['baseline_sessions']} baseline sessions.",
                session_total, session_total, None, frequency["confidence"],
            ),
            self._coverage_entry(
                "Program Execution", "Available" if program_available else "Unavailable",
                (
                    f"{program['matched_completed_opportunities']} of {program['eligible_opportunities']} linked opportunities completed."
                    if program_available else "No trustworthy linked planned opportunities."
                ),
                program["confidence"]["sample_count"],
                program["confidence"]["eligible_sample_count"],
                program["execution_percent"], program["confidence"],
            ),
            self._coverage_entry(
                "Performance", "Available" if performance_available else "Unavailable",
                f"{performance['exercises_observed']} exercise histories available.",
                performance["exercises_observed"], performance["exercises_observed"], None,
                _confidence(sample_count=performance["exercises_observed"], required=2),
            ),
            self._coverage_entry(
                "Workload", "Available" if workload_available else ("Limited" if frequency["recent_sessions"] else "Unavailable"),
                "Comparable exercise workload is available." if workload_available else "No comparable prior workload baseline.",
                session_total, workload["comparable_exercise_count"],
                workload["exercise_mix_coverage_percent"], workload["confidence"],
            ),
            self._coverage_entry(
                "Nutrition Coverage", "Available" if complete_days >= 3 else ("Limited" if nutrition["days_with_any_logs"] else "Unavailable"),
                f"{complete_days} explicitly complete days; {nutrition['days_with_any_logs']} days with any logs.",
                nutrition["days_with_any_logs"], complete_days,
                nutrition["complete_day_coverage_percent"],
                _confidence(sample_count=nutrition["days_with_any_logs"], eligible_count=complete_days, coverage_percent=nutrition["complete_day_coverage_percent"], required=3),
            ),
            self._coverage_entry(
                "Nutrition Targets", "Available" if target_configured and complete_days >= 3 else ("Limited" if target_configured else "Unavailable"),
                "Configured targets are compared only with complete eligible days." if target_configured else "No user-configured nutrition target.",
                nutrition["days_with_any_logs"], complete_days,
                nutrition["complete_day_coverage_percent"],
                _confidence(sample_count=nutrition["days_with_any_logs"], eligible_count=complete_days, coverage_percent=nutrition["complete_day_coverage_percent"], required=3),
            ),
        ]

    def _data_through(self, source: dict[str, Any]) -> datetime:
        values: list[datetime] = []
        for workout in source["workouts"]:
            completed = _as_utc(workout.completed_at)
            if completed:
                values.append(completed)
        for log in source["nutrition"]:
            local_value = datetime.combine(log.date, time.max, tzinfo=self.user_tz)
            values.append(local_value.astimezone(timezone.utc))
        for status in source["day_statuses"]:
            updated = _as_utc(status.updated_at)
            if updated:
                values.append(updated)
        for schedule in source["schedules"]:
            created = _as_utc(schedule.created_at)
            if created:
                values.append(created)
        return min(max(values, default=self.now), self.now)

    def _prune_snapshots(self) -> None:
        rows = (
            self.db.query(LabAnalysisSnapshot)
            .filter(LabAnalysisSnapshot.user_id == self.user.id)
            .order_by(LabAnalysisSnapshot.generated_at.desc())
            .all()
        )
        for row in rows[5:]:
            self.db.delete(row)
        self.db.commit()


def exact_program_opportunities(
    schedules: Iterable[ScheduledWorkout], start: date, end: date
) -> tuple[int, int]:
    """Public pure helper used by regression tests for 7/14/30-day math."""
    eligible = [
        item for item in schedules
        if start <= item.scheduled_date <= end
        and item.linkage_trustworthy
        and item.status in {"planned", "completed"}
    ]
    matched = [item for item in eligible if item.status == "completed"]
    return len(matched), len(eligible)
