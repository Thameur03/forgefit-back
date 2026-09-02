"""Trustworthy, documented product-operations metric calculations.

Calendar-day semantics are UTC. Core workout/nutrition activity comes from the
transactional tables, while interaction-only features come from the canonical
analytics event stream. Keeping those sources explicit prevents attractive UI
from disguising missing instrumentation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
import re
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.analytics_event import AnalyticsEvent
from models.food import Food
from models.nutrition import NutritionLog
from models.program import Program
from models.schedule import ScheduledWorkout
from models.user import User
from models.workout import Workout, WorkoutSet
from services.analytics_events import (
    FAILURE_EVENT_NAMES,
    MEANINGFUL_ACTIVITY_EVENTS,
    safe_stored_event_properties,
)


UTC = timezone.utc
_SAFE_STRUCTURED_LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _./:+-]{0,99}$")


@dataclass(frozen=True)
class DateRange:
    start_date: date
    end_date: date

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start_date, time.min, tzinfo=UTC)

    @property
    def end_at(self) -> datetime:
        return datetime.combine(self.end_date + timedelta(days=1), time.min, tzinfo=UTC)

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def payload(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "timezone": "UTC",
        }


def resolve_date_range(
    db: Session,
    *,
    start_date: date | None,
    end_date: date | None,
    period: str,
) -> DateRange:
    today = datetime.now(UTC).date()
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "start_date and end_date must be provided together",
        )
    if start_date is not None and end_date is not None:
        start, end = start_date, end_date
    else:
        end = today
        presets = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
        if period == "all":
            earliest = db.query(func.min(User.created_at)).scalar()
            start = _as_utc(earliest).date() if earliest else today
        elif period in presets:
            start = end - timedelta(days=presets[period] - 1)
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "period must be one of: today, 7d, 30d, 90d, all",
            )
    if end > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date cannot be in the future")
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start_date must not exceed end_date")
    if (end - start).days > 1825:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date range cannot exceed five years")
    return DateRange(start, end)


def previous_range(value: DateRange) -> DateRange:
    end = value.start_date - timedelta(days=1)
    return DateRange(end - timedelta(days=value.days - 1), end)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _day(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return _as_utc(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def _percent(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 2)


def _change(current: int | float, previous: int | float) -> float | None:
    if previous == 0:
        return None if current == 0 else 100.0
    return round((float(current) - float(previous)) / float(previous) * 100.0, 2)


def _dates(value: DateRange) -> list[date]:
    return [value.start_date + timedelta(days=offset) for offset in range(value.days)]


def _normal_user_ids(db: Session) -> set[int]:
    return {row[0] for row in db.query(User.id).filter(User.role == "user").all()}


def meaningful_user_days(
    db: Session, start_date: date, end_date: date
) -> set[tuple[int, date]]:
    """Distinct meaningful user/calendar-day pairs from canonical sources."""
    result: set[tuple[int, date]] = set()
    workout_rows = (
        db.query(Workout.user_id, Workout.date)
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.date >= start_date,
            Workout.date <= end_date,
        )
        .all()
    )
    result.update((row.user_id, _day(row.date)) for row in workout_rows)
    nutrition_rows = (
        db.query(NutritionLog.user_id, NutritionLog.date)
        .join(User, User.id == NutritionLog.user_id)
        .filter(
            User.role == "user",
            NutritionLog.date >= start_date,
            NutritionLog.date <= end_date,
        )
        .all()
    )
    result.update((row.user_id, _day(row.date)) for row in nutrition_rows)
    bounds = DateRange(start_date, end_date)
    event_rows = (
        db.query(AnalyticsEvent.user_id, AnalyticsEvent.occurred_at)
        .join(User, User.id == AnalyticsEvent.user_id)
        .filter(
            User.role == "user",
            AnalyticsEvent.user_id.is_not(None),
            AnalyticsEvent.event_name.in_(MEANINGFUL_ACTIVITY_EVENTS),
            AnalyticsEvent.occurred_at >= bounds.start_at,
            AnalyticsEvent.occurred_at < bounds.end_at,
        )
        .all()
    )
    result.update((row.user_id, _day(row.occurred_at)) for row in event_rows)
    return result


def _event_rows(
    db: Session,
    value: DateRange,
    names: Iterable[str] | None = None,
) -> list[AnalyticsEvent]:
    query = db.query(AnalyticsEvent).filter(
        AnalyticsEvent.occurred_at >= value.start_at,
        AnalyticsEvent.occurred_at < value.end_at,
    )
    if names is not None:
        query = query.filter(AnalyticsEvent.event_name.in_(set(names)))
    return query.all()


def _event_count(db: Session, value: DateRange, name: str) -> int:
    return (
        db.query(func.count(AnalyticsEvent.id))
        .filter(
            AnalyticsEvent.event_name == name,
            AnalyticsEvent.occurred_at >= value.start_at,
            AnalyticsEvent.occurred_at < value.end_at,
        )
        .scalar()
        or 0
    )


def _event_users(db: Session, value: DateRange, names: Iterable[str]) -> set[int]:
    return {
        row[0]
        for row in db.query(AnalyticsEvent.user_id)
        .join(User, User.id == AnalyticsEvent.user_id)
        .filter(
            User.role == "user",
            AnalyticsEvent.user_id.is_not(None),
            AnalyticsEvent.event_name.in_(set(names)),
            AnalyticsEvent.occurred_at >= value.start_at,
            AnalyticsEvent.occurred_at < value.end_at,
        )
        .distinct()
        .all()
    }


def active_counts_at(db: Session, end_date: date) -> tuple[int, int, int]:
    days = meaningful_user_days(db, end_date - timedelta(days=29), end_date)
    dau = {user_id for user_id, day in days if day == end_date}
    wau_start = end_date - timedelta(days=6)
    wau = {user_id for user_id, day in days if day >= wau_start}
    mau = {user_id for user_id, _ in days}
    return len(dau), len(wau), len(mau)


def activation_for_signup_cohort(
    db: Session, value: DateRange
) -> tuple[int, int, set[int]]:
    users = (
        db.query(User)
        .filter(
            User.role == "user",
            User.created_at >= value.start_at,
            User.created_at < value.end_at,
        )
        .all()
    )
    cohort_ids = {user.id for user in users}
    if not cohort_ids:
        return 0, 0, set()
    onboarding = {
        row[0]
        for row in db.query(AnalyticsEvent.user_id)
        .filter(
            AnalyticsEvent.user_id.in_(cohort_ids),
            AnalyticsEvent.event_name == "onboarding_completed",
            AnalyticsEvent.occurred_at < value.end_at,
        )
        .distinct()
        .all()
    }
    core = {
        row[0]
        for row in db.query(Workout.user_id)
        .filter(
            Workout.user_id.in_(cohort_ids),
            Workout.completed_at.is_not(None),
            Workout.date <= value.end_date,
        )
        .distinct()
        .all()
    }
    core.update(
        row[0]
        for row in db.query(NutritionLog.user_id)
        .filter(
            NutritionLog.user_id.in_(cohort_ids),
            NutritionLog.date <= value.end_date,
        )
        .distinct()
        .all()
    )
    verified = {
        user.id
        for user in users
        if user.is_verified
        and user.verified_at is not None
        and _as_utc(user.verified_at) < value.end_at
    }
    activated = verified & onboarding & core
    return len(activated), len(users), activated


def retention_summary_for_users(
    users: Iterable[User],
    activity_days: set[tuple[int, date]],
    *,
    as_of: date,
) -> list[dict[str, Any]]:
    activity = defaultdict(set)
    for user_id, activity_date in activity_days:
        activity[user_id].add(activity_date)
    user_list = list(users)
    result = []
    for day_number in (1, 7, 14, 30):
        eligible = [
            user
            for user in user_list
            if _day(user.created_at) + timedelta(days=day_number) <= as_of
        ]
        retained = sum(
            1
            for user in eligible
            if _day(user.created_at) + timedelta(days=day_number)
            in activity.get(user.id, set())
        )
        result.append(
            {
                "day": day_number,
                "eligible_users": len(eligible),
                "retained_users": retained,
                "rate": _percent(retained, len(eligible)),
            }
        )
    return result


def executive_overview(db: Session, value: DateRange) -> dict[str, Any]:
    prior = previous_range(value)
    total_users = db.query(func.count(User.id)).filter(User.role == "user").scalar() or 0
    verified_users = (
        db.query(func.count(User.id))
        .filter(User.role == "user", User.is_verified.is_(True))
        .scalar()
        or 0
    )
    new_users = (
        db.query(func.count(User.id))
        .filter(
            User.role == "user",
            User.created_at >= value.start_at,
            User.created_at < value.end_at,
        )
        .scalar()
        or 0
    )
    prior_new = (
        db.query(func.count(User.id))
        .filter(
            User.role == "user",
            User.created_at >= prior.start_at,
            User.created_at < prior.end_at,
        )
        .scalar()
        or 0
    )
    dau, wau, mau = active_counts_at(db, value.end_date)
    prior_dau, prior_wau, prior_mau = active_counts_at(db, prior.end_date)
    activated, activation_denominator, _ = activation_for_signup_cohort(db, value)
    prior_activated, prior_activation_denominator, _ = activation_for_signup_cohort(db, prior)
    workouts = (
        db.query(func.count(Workout.id))
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.date >= value.start_date,
            Workout.date <= value.end_date,
        )
        .scalar()
        or 0
    )
    inferred_workouts = (
        db.query(func.count(Workout.id))
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.completion_inferred.is_(True),
            Workout.date >= value.start_date,
            Workout.date <= value.end_date,
        )
        .scalar()
        or 0
    )
    prior_workouts = (
        db.query(func.count(Workout.id))
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.date >= prior.start_date,
            Workout.date <= prior.end_date,
        )
        .scalar()
        or 0
    )
    nutrition_rows = (
        db.query(NutritionLog.user_id, NutritionLog.date, NutritionLog.meal_name)
        .join(User, User.id == NutritionLog.user_id)
        .filter(
            User.role == "user",
            NutritionLog.date >= value.start_date,
            NutritionLog.date <= value.end_date,
        )
        .all()
    )
    meals = len({(r.user_id, _day(r.date), r.meal_name) for r in nutrition_rows})
    prior_nutrition = (
        db.query(NutritionLog.user_id, NutritionLog.date, NutritionLog.meal_name)
        .join(User, User.id == NutritionLog.user_id)
        .filter(
            User.role == "user",
            NutritionLog.date >= prior.start_date,
            NutritionLog.date <= prior.end_date,
        )
        .all()
    )
    prior_meals = len(
        {(r.user_id, _day(r.date), r.meal_name) for r in prior_nutrition}
    )
    active_programs = (
        db.query(func.count(Program.id))
        .join(User, User.id == Program.user_id)
        .filter(User.role == "user", Program.is_active.is_(True))
        .scalar()
        or 0
    )
    cohort_users = (
        db.query(User)
        .filter(
            User.role == "user",
            User.created_at >= value.start_at,
            User.created_at < value.end_at,
        )
        .all()
    )
    activity = meaningful_user_days(
        db, value.start_date, min(datetime.now(UTC).date(), value.end_date + timedelta(days=30))
    )
    d7 = retention_summary_for_users(cohort_users, activity, as_of=value.end_date)[1]

    def metric(
        current: float,
        previous: float | None = None,
        *,
        unit: str = "count",
        trustworthy: bool = True,
        limitation: str | None = None,
    ) -> dict[str, Any]:
        return {
            "value": round(float(current), 2),
            "previous_value": round(float(previous), 2) if previous is not None else None,
            "change_percent": _change(current, previous) if previous is not None else None,
            "unit": unit,
            "trustworthy": trustworthy,
            "limitation": limitation,
        }

    activation_rate = _percent(activated, activation_denominator) or 0.0
    prior_activation_rate = _percent(prior_activated, prior_activation_denominator) or 0.0
    return {
        "date_range": value.payload(),
        "metrics": {
            "total_users": metric(total_users),
            "new_users": metric(new_users, prior_new),
            "verified_percentage": metric(
                _percent(verified_users, total_users) or 0.0, unit="percent"
            ),
            "dau": metric(dau, prior_dau),
            "wau": metric(wau, prior_wau),
            "mau": metric(mau, prior_mau),
            "activated_users": metric(
                activated,
                prior_activated,
                trustworthy=False,
                limitation="Historical users require onboarding_completed instrumentation.",
            ),
            "activation_rate": metric(
                activation_rate,
                prior_activation_rate,
                unit="percent",
                trustworthy=False,
                limitation="Cohort is accounts created in the selected range.",
            ),
            "d7_retention": metric(
                d7["rate"] or 0.0,
                unit="percent",
                limitation="Only signup-cohort members old enough for D7 are eligible.",
            ),
            "workouts_completed": metric(
                workouts,
                prior_workouts,
                trustworthy=inferred_workouts == 0,
                limitation=(
                    f"{inferred_workouts} legacy workout(s) were conservatively inferred from positive final duration."
                    if inferred_workouts
                    else None
                ),
            ),
            "meals_logged": metric(
                meals,
                prior_meals,
                limitation="Distinct user/date/meal label; the schema stores food entries, not meal entities.",
            ),
            "active_programs": metric(
                active_programs,
                trustworthy=True,
                limitation="Current snapshot; programs have no created/activated timestamps.",
            ),
        },
        "data_quality": [
            "Workout activity requires explicit finalization; legacy positive-duration rows are marked as inferred.",
            "Nutrition activity uses transactional records, not client event delivery.",
            "Activation and post-signup funnel stages are complete only after the new instrumentation rollout.",
            "Deleted accounts cannot be reconstructed in historical user-growth totals.",
        ],
    }


def growth(db: Session, value: DateRange) -> dict[str, Any]:
    users = (
        db.query(User)
        .filter(User.role == "user", User.created_at < value.end_at)
        .order_by(User.created_at)
        .all()
    )
    before = sum(1 for user in users if _day(user.created_at) < value.start_date)
    by_day: dict[date, list[User]] = defaultdict(list)
    for user in users:
        joined = _day(user.created_at)
        if value.start_date <= joined <= value.end_date:
            by_day[joined].append(user)
    running = before
    points = []
    for day_value in _dates(value):
        rows = by_day.get(day_value, [])
        running += len(rows)
        points.append(
            {
                "date": day_value,
                "new_users": len(rows),
                "verified_new_users": sum(1 for user in rows if user.is_verified),
                "cumulative_users": running,
            }
        )
    prior = previous_range(value)
    previous_count = (
        db.query(func.count(User.id))
        .filter(
            User.role == "user",
            User.created_at >= prior.start_at,
            User.created_at < prior.end_at,
        )
        .scalar()
        or 0
    )
    current_count = sum(point["new_users"] for point in points)
    return {
        "date_range": value.payload(),
        "points": points,
        "total_new_users": current_count,
        "previous_period_new_users": previous_count,
        "change_percent": _change(current_count, previous_count),
    }


def signup_funnel(db: Session, value: DateRange) -> dict[str, Any]:
    stage_names = [
        ("started", "Signup Started", "signup_started"),
        ("summary", "Signup Summary Viewed", "signup_summary_viewed"),
        ("submit", "Signup Submit Clicked", "signup_submit_clicked"),
        ("created", "Account Created", "signup_completed"),
        ("verified", "Email Verified", "email_verification_completed"),
        ("onboarded", "Onboarding Completed", "onboarding_completed"),
    ]
    # Cohort is selected by signup_started time. Follow-up stages may occur
    # later, so read them through today rather than truncating a historical
    # cohort at its acquisition-period boundary.
    follow_end = datetime.now(UTC).date()
    follow_range = DateRange(value.start_date, follow_end)
    rows = _event_rows(db, follow_range, [stage[2] for stage in stage_names])

    def identity(row: AnalyticsEvent) -> str | None:
        if row.user_id is not None:
            return f"u:{row.user_id}"
        if row.session_id:
            return f"s:{row.session_id}"
        return None

    started = {
        identity(row)
        for row in rows
        if row.event_name == "signup_started"
        and value.start_at <= _as_utc(row.occurred_at) < value.end_at
        and identity(row) is not None
    }
    reached: dict[str, set[str]] = {"started": set(started)}
    prior_keys = set(started)
    for key, _label, event_name in stage_names[1:]:
        if key == "verified":
            verified_users = {
                f"u:{row[0]}"
                for row in db.query(User.id)
                .filter(User.is_verified.is_(True))
                .all()
            }
            prior_keys = prior_keys & verified_users
            reached[key] = set(prior_keys)
            continue
        event_keys = {
            identity(row)
            for row in rows
            if row.event_name == event_name and identity(row) is not None
        }
        prior_keys = prior_keys & event_keys
        reached[key] = set(prior_keys)

    activated_ids = {
        int(key.split(":", 1)[1])
        for key in reached.get("onboarded", set())
        if key.startswith("u:")
    }
    if activated_ids:
        verified = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(activated_ids), User.is_verified.is_(True))
            .all()
        }
        core = {
            row[0]
            for row in db.query(Workout.user_id)
            .filter(
                Workout.user_id.in_(activated_ids),
                Workout.completed_at.is_not(None),
            )
            .distinct()
            .all()
        }
        core.update(
            row[0]
            for row in db.query(NutritionLog.user_id)
            .filter(NutritionLog.user_id.in_(activated_ids))
            .distinct()
            .all()
        )
        activated_ids = verified & core
    reached["activated"] = {f"u:{user_id}" for user_id in activated_ids}

    activated_users = (
        db.query(User).filter(User.id.in_(activated_ids)).all() if activated_ids else []
    )
    activity = meaningful_user_days(db, value.start_date, follow_end)
    activity_map: dict[int, set[date]] = defaultdict(set)
    for user_id, activity_date in activity:
        activity_map[user_id].add(activity_date)
    mature_ids = {
        user.id
        for user in activated_users
        if _day(user.created_at) + timedelta(days=7) <= follow_end
    }
    returned_ids = {
        user.id
        for user in activated_users
        if user.id in mature_ids
        and _day(user.created_at) + timedelta(days=7)
        in activity_map.get(user.id, set())
    }
    reached["returned_d7"] = {f"u:{user_id}" for user_id in returned_ids}

    ordered = [
        ("started", "Signup Started"),
        ("summary", "Signup Summary Viewed"),
        ("submit", "Signup Submit Clicked"),
        ("created", "Account Created"),
        ("verified", "Email Verified"),
        ("onboarded", "Onboarding Completed"),
        ("activated", "Activated"),
        ("returned_d7", "Returned D7"),
    ]
    stages = []
    start_count = len(reached["started"])
    previous_count: int | None = None
    for key, label in ordered:
        count = len(reached[key])
        denominator = previous_count
        eligible = len(mature_ids) if key == "returned_d7" else None
        if key == "returned_d7":
            denominator = eligible
        stages.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "eligible_count": eligible,
                "conversion_from_previous": _percent(count, denominator) if denominator is not None else None,
                "conversion_from_start": _percent(count, start_count),
                "drop_off_percent": (
                    round(100.0 - (_percent(count, denominator) or 0.0), 2)
                    if denominator not in (None, 0)
                    else None
                ),
            }
        )
        previous_count = count
    return {
        "date_range": value.payload(),
        "stages": stages,
        "identity_semantics": (
            "Exact session identity before authentication; user identity after the "
            "authenticated client links that same anonymous_id/session_id."
        ),
        "data_quality": [
            "Counts are sequential intersections, so later stages cannot exceed earlier stages.",
            "D7 uses exact UTC calendar day 7 and excludes users not yet old enough from its eligible denominator.",
            "Pre-rollout email verification/onboarding events cannot be reconstructed reliably.",
        ],
    }


def retention(db: Session, value: DateRange) -> dict[str, Any]:
    cohort_users = (
        db.query(User)
        .filter(
            User.role == "user",
            User.created_at >= value.start_at,
            User.created_at < value.end_at,
        )
        .all()
    )
    as_of = value.end_date
    earliest_activity = value.start_date - timedelta(days=30)
    activity = meaningful_user_days(db, earliest_activity, value.end_date)
    summary = retention_summary_for_users(cohort_users, activity, as_of=as_of)
    dau, wau, mau = active_counts_at(db, value.end_date)
    activity_series = []
    for point_date in _dates(value):
        point_dau = {uid for uid, day_value in activity if day_value == point_date}
        point_wau = {
            uid
            for uid, day_value in activity
            if point_date - timedelta(days=6) <= day_value <= point_date
        }
        point_mau = {
            uid
            for uid, day_value in activity
            if point_date - timedelta(days=29) <= day_value <= point_date
        }
        activity_series.append(
            {
                "date": point_date,
                "dau": len(point_dau),
                "wau": len(point_wau),
                "mau": len(point_mau),
            }
        )

    by_week: dict[date, list[User]] = defaultdict(list)
    for user in cohort_users:
        joined = _day(user.created_at)
        week = joined - timedelta(days=joined.weekday())
        by_week[week].append(user)
    cohorts = []
    for week in sorted(by_week):
        users = by_week[week]
        metrics = retention_summary_for_users(users, activity, as_of=as_of)
        by_n = {item["day"]: item for item in metrics}
        cohorts.append(
            {
                "cohort_week": week,
                "cohort_size": len(users),
                "d1": by_n[1],
                "d7": by_n[7],
                "d14": by_n[14],
                "d30": by_n[30],
            }
        )
    return {
        "date_range": value.payload(),
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "dau_mau_stickiness": _percent(dau, mau) or 0.0,
        "wau_mau_stickiness": _percent(wau, mau) or 0.0,
        "activity_series": activity_series,
        "summary": summary,
        "cohorts": cohorts,
        "semantics": (
            "Signup-date cohorts. Dn retained means at least one meaningful action "
            "on exactly UTC calendar day n after account creation; immature members "
            "are excluded from that Dn denominator. Legacy positive-duration "
            "workouts are completion-inferred and zero-duration legacy rows are excluded."
        ),
    }


def feature_adoption(db: Session, value: DateRange) -> dict[str, Any]:
    activity = meaningful_user_days(db, value.start_date, value.end_date)
    active = {user_id for user_id, _ in activity}
    workout = {
        row[0]
        for row in db.query(Workout.user_id)
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.date >= value.start_date,
            Workout.date <= value.end_date,
        )
        .distinct()
        .all()
    }
    nutrition = {
        row[0]
        for row in db.query(NutritionLog.user_id)
        .join(User, User.id == NutritionLog.user_id)
        .filter(
            User.role == "user",
            NutritionLog.date >= value.start_date,
            NutritionLog.date <= value.end_date,
        )
        .distinct()
        .all()
    }
    program = _event_users(
        db,
        value,
        {"program_viewed", "program_created", "program_activated", "program_changed"},
    )
    calendar = _event_users(
        db,
        value,
        {"workout_scheduled", "scheduled_workout_completed", "scheduled_workout_cancelled"},
    )
    calendar.update(
        row[0]
        for row in db.query(ScheduledWorkout.user_id)
        .join(User, User.id == ScheduledWorkout.user_id)
        .filter(
            User.role == "user",
            ScheduledWorkout.created_at >= value.start_at,
            ScheduledWorkout.created_at < value.end_at,
        )
        .distinct()
        .all()
    )
    sets = {
        "workouts": workout,
        "programs": program,
        "calendar": calendar,
        "nutrition": nutrition,
        "barcode": _event_users(db, value, {"barcode_scan_used"}),
        "micronutrients": _event_users(db, value, {"micronutrients_viewed"}),
        "statistics": _event_users(db, value, {"stats_viewed"}),
        "personal_records": _event_users(db, value, {"personal_record_achieved"}),
        "lab_insights": _event_users(
            db,
            value,
            {
                "lab_insights_viewed",
                "insight_refreshed",
                "insight_impression",
                "lab_insight_generated",
            },
        ),
    }
    labels = {
        "workouts": "Workout tracking",
        "programs": "Programs",
        "calendar": "Calendar / scheduling",
        "nutrition": "Nutrition",
        "barcode": "Barcode scanner",
        "micronutrients": "Micronutrients",
        "statistics": "Statistics",
        "personal_records": "Personal records",
        "lab_insights": "Lab Insights",
    }
    sources = {
        "workouts": "workouts table",
        "nutrition": "nutrition_logs table",
        "calendar": "scheduled_workouts + canonical events",
    }
    features = []
    for key, users in sets.items():
        adopted = users & active
        features.append(
            {
                "key": key,
                "label": labels[key],
                "users": len(adopted),
                "active_user_percentage": _percent(len(adopted), len(active)),
                "source": sources.get(key, "canonical analytics events"),
                "limitation": None
                if key in {"workouts", "nutrition"}
                else "Historical usage before instrumentation is unavailable.",
            }
        )
    workout_active = workout & active
    nutrition_active = nutrition & active
    return {
        "date_range": value.payload(),
        "active_users": len(active),
        "features": features,
        "core_feature_split": {
            "workout_only": len(workout_active - nutrition_active),
            "nutrition_only": len(nutrition_active - workout_active),
            "workout_and_nutrition": len(workout_active & nutrition_active),
            "neither_core": len(active - workout_active - nutrition_active),
        },
        "data_quality": [
            "Workout adoption requires completed_at; pre-migration positive-duration rows are marked as inferred completions.",
            "Nutrition adoption is calculated from transactional nutrition records.",
            "Interaction-only feature adoption begins at instrumentation rollout.",
        ],
    }


def workout_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    workouts = (
        db.query(Workout)
        .join(User, User.id == Workout.user_id)
        .filter(
            User.role == "user",
            Workout.completed_at.is_not(None),
            Workout.date >= value.start_date,
            Workout.date <= value.end_date,
        )
        .all()
    )
    workout_ids = [workout.id for workout in workouts]
    sets = (
        db.query(WorkoutSet).filter(WorkoutSet.workout_id.in_(workout_ids)).all()
        if workout_ids
        else []
    )
    unique_users = {workout.user_id for workout in workouts}
    duration_values = [workout.duration_seconds for workout in workouts if workout.duration_seconds and workout.duration_seconds > 0]
    total_sets = sum(max(0, item.sets) for item in sets)
    volume = sum(
        max(0, item.sets) * max(0, item.reps) * max(0.0, item.weight_kg or 0.0)
        for item in sets
    )
    catalog_exercises: Counter[tuple[str, str]] = Counter()
    for item in sets:
        if item.exercise_id and _SAFE_STRUCTURED_LABEL.fullmatch(item.exercise_name):
            catalog_exercises[(item.exercise_id, item.exercise_name)] += max(0, item.sets)
    schedules = (
        db.query(ScheduledWorkout.user_id, ScheduledWorkout.scheduled_date)
        .join(User, User.id == ScheduledWorkout.user_id)
        .filter(
            User.role == "user",
            ScheduledWorkout.scheduled_date >= value.start_date,
            ScheduledWorkout.scheduled_date <= value.end_date,
        )
        .all()
    )
    schedule_keys = {(row.user_id, _day(row.scheduled_date)) for row in schedules}
    workout_keys = {(row.user_id, _day(row.date)) for row in workouts}
    matched = len(schedule_keys & workout_keys)
    by_day: Counter[date] = Counter(_day(workout.date) for workout in workouts)
    users_by_day: dict[date, set[int]] = defaultdict(set)
    for workout in workouts:
        users_by_day[_day(workout.date)].add(workout.user_id)
    weeks = max(value.days / 7.0, 1 / 7.0)
    return {
        "date_range": value.payload(),
        "completed_workouts": len(workouts),
        "unique_workout_users": len(unique_users),
        "workouts_per_active_user": round(len(workouts) / len(unique_users), 2) if unique_users else 0.0,
        "average_workouts_per_user_week": round(len(workouts) / len(unique_users) / weeks, 2) if unique_users else 0.0,
        "average_duration_minutes": round(sum(duration_values) / len(duration_values) / 60.0, 2) if duration_values else None,
        "duration_sample_size": len(duration_values),
        "total_sets": total_sets,
        "total_training_volume_kg": round(volume, 2),
        "scheduled_workouts_matched": matched,
        "unscheduled_workouts": max(0, len(workouts) - matched),
        "scheduled_completion_rate": _percent(matched, len(schedule_keys)),
        "personal_records": _event_count(db, value, "personal_record_achieved"),
        "series": [
            {"date": day_value, "count": by_day[day_value], "unique_users": len(users_by_day[day_value])}
            for day_value in _dates(value)
        ],
        "top_exercises": [
            {"key": key, "label": label, "count": count}
            for (key, label), count in catalog_exercises.most_common(10)
        ],
        "data_quality": [
            "Completed workouts require completed_at; legacy positive-duration rows are explicitly marked completion-inferred.",
            "Scheduled matching uses user + calendar date because workouts do not store scheduled_workout_id.",
            "Custom/free-text exercises are excluded from rankings.",
            "Program-vs-custom workout attribution is unavailable in the workout schema.",
        ],
    }


def nutrition_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    rows = (
        db.query(NutritionLog)
        .join(User, User.id == NutritionLog.user_id)
        .filter(
            User.role == "user",
            NutritionLog.date >= value.start_date,
            NutritionLog.date <= value.end_date,
        )
        .all()
    )
    users = {row.user_id for row in rows}
    logging_days = {(row.user_id, _day(row.date)) for row in rows}
    meals = {(row.user_id, _day(row.date), row.meal_name) for row in rows}
    by_day: Counter[date] = Counter(_day(row.date) for row in rows)
    users_by_day: dict[date, set[int]] = defaultdict(set)
    fdc_counts: Counter[int] = Counter()
    for row in rows:
        users_by_day[_day(row.date)].add(row.user_id)
        if row.fdc_id is not None:
            fdc_counts[row.fdc_id] += 1
    known_foods = {
        row.fdc_id: row.name
        for row in db.query(Food).filter(Food.fdc_id.in_(list(fdc_counts))).all()
        if row.fdc_id is not None
    } if fdc_counts else {}
    return {
        "date_range": value.payload(),
        "nutrition_entries": len(rows),
        "meals_logged": len(meals),
        "unique_nutrition_users": len(users),
        "nutrition_logging_days": len(logging_days),
        "average_logging_days_per_active_user": round(len(logging_days) / len(users), 2) if users else 0.0,
        "barcode_uses": _event_count(db, value, "barcode_scan_used"),
        "manual_food_adds": _event_count(db, value, "manual_food_added"),
        "food_searches": _event_count(db, value, "food_search_performed"),
        "food_search_failures": _event_count(db, value, "food_search_failed"),
        "macro_goal_users": len(_event_users(db, value, {"macro_goals_viewed"})),
        "micronutrient_users": len(_event_users(db, value, {"micronutrients_viewed"})),
        "series": [
            {"date": day_value, "count": by_day[day_value], "unique_users": len(users_by_day[day_value])}
            for day_value in _dates(value)
        ],
        "top_catalog_foods": [
            {
                "key": str(fdc_id),
                "label": known_foods.get(fdc_id, f"USDA #{fdc_id}"),
                "count": count,
            }
            for fdc_id, count in fdc_counts.most_common(10)
        ],
        "data_quality": [
            "Nutrition entries come from persisted logs.",
            "Meals are distinct user/date/meal-label groups because no meal entity exists.",
            "Only catalog/USDA identifiers are ranked; free-text food names are never returned.",
            "Search, barcode, macro, and micronutrient usage begins at instrumentation rollout.",
        ],
    }


def program_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    programs = db.query(Program).join(User, User.id == Program.user_id).filter(User.role == "user").all()
    active = [program for program in programs if program.is_active]
    active_users = {program.user_id for program in active}
    total_users = db.query(func.count(User.id)).filter(User.role == "user").scalar() or 0
    template_counts: Counter[str] = Counter()
    for program in programs:
        source = program.source_template
        if source and _SAFE_STRUCTURED_LABEL.fullmatch(source):
            template_counts[source] += 1
    return {
        "date_range": value.payload(),
        "template_activations": _event_count(db, value, "program_activated"),
        "active_programs": len(active),
        "users_with_active_program": len(active_users),
        "users_without_active_program": max(0, total_users - len(active_users)),
        "custom_programs": sum(1 for program in programs if not program.source_template),
        "template_programs": sum(1 for program in programs if program.source_template),
        "program_changes": _event_count(db, value, "program_changed"),
        "most_used_templates": [
            {"key": source, "label": source, "count": count}
            for source, count in template_counts.most_common(10)
        ],
        "data_quality": [
            "Active/custom/template program counts are current snapshots.",
            "Program rows do not have creation or activation timestamps; date-range trends are event-based going forward.",
        ],
    }


def scheduling_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    rows = (
        db.query(ScheduledWorkout)
        .join(User, User.id == ScheduledWorkout.user_id)
        .filter(
            User.role == "user",
            ScheduledWorkout.created_at >= value.start_at,
            ScheduledWorkout.created_at < value.end_at,
        )
        .all()
    )
    today = datetime.now(UTC).date()
    creation_events = _event_rows(db, value, {"workout_scheduled"})
    follow_events = _event_rows(
        db,
        DateRange(value.start_date, today),
        {"scheduled_workout_completed", "scheduled_workout_cancelled"},
    )

    def schedule_id(event: AnalyticsEvent) -> int | None:
        raw = (event.properties or {}).get("schedule_id")
        return raw if isinstance(raw, int) and raw > 0 else None

    created_by_id: dict[int, tuple[date, int]] = {
        row.id: (_day(row.created_at), row.user_id) for row in rows
    }
    for event in creation_events:
        event_schedule_id = schedule_id(event)
        if event_schedule_id is not None and event.user_id is not None:
            created_by_id[event_schedule_id] = (
                _day(event.occurred_at),
                event.user_id,
            )
    cohort_ids = set(created_by_id)
    completed_ids = {
        event_schedule_id
        for event in follow_events
        if event.event_name == "scheduled_workout_completed"
        and (event_schedule_id := schedule_id(event)) in cohort_ids
    }
    cancelled_ids = {
        event_schedule_id
        for event in follow_events
        if event.event_name == "scheduled_workout_cancelled"
        and (event_schedule_id := schedule_id(event)) in cohort_ids
    }
    by_day: Counter[date] = Counter(
        created_date for created_date, _user_id in created_by_id.values()
    )
    users_by_day: dict[date, set[int]] = defaultdict(set)
    for created_date, user_id in created_by_id.values():
        users_by_day[created_date].add(user_id)
    all_completed_ids = {
        event_schedule_id
        for event in _event_rows(
            db,
            DateRange(today, today),
            {"scheduled_workout_completed"},
        )
        if (event_schedule_id := schedule_id(event)) is not None
    }
    upcoming = (
        db.query(func.count(ScheduledWorkout.id))
        .join(User, User.id == ScheduledWorkout.user_id)
        .filter(
            User.role == "user",
            ScheduledWorkout.scheduled_date >= datetime.now(UTC).date(),
            ScheduledWorkout.id.not_in(all_completed_ids),
        )
        .scalar()
        or 0
    )
    denominator = len(cohort_ids)
    return {
        "date_range": value.payload(),
        "scheduled_workouts": denominator,
        "unique_scheduling_users": len(
            {user_id for _created_date, user_id in created_by_id.values()}
        ),
        "completed_events": len(completed_ids),
        "cancelled_events": len(cancelled_ids),
        "scheduled_to_completed_rate": _percent(len(completed_ids), denominator),
        "upcoming_scheduled_count": upcoming,
        "series": [
            {"date": day_value, "count": by_day[day_value], "unique_users": len(users_by_day[day_value])}
            for day_value in _dates(value)
        ],
        "data_quality": [
            "Creation counts combine persisted schedules with id-correlated creation events so deleted schedules remain in the cohort.",
            "Completion and cancellation require the matching privacy-safe schedule_id and are incomplete before instrumentation rollout.",
            "The schedule table has no completion/cancellation status history.",
        ],
    }


def insights_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    eligible_ids = {
        row[0]
        for row in db.query(User.id)
        .filter(User.role == "user", User.created_at < value.end_at)
        .all()
    }
    history_ids = {
        row[0]
        for row in db.query(Workout.user_id)
        .filter(
            Workout.user_id.in_(eligible_ids),
            Workout.completed_at.is_not(None),
            Workout.date <= value.end_date,
        )
        .distinct()
        .all()
    } if eligible_ids else set()
    if eligible_ids:
        history_ids.update(
            row[0]
            for row in db.query(NutritionLog.user_id)
            .filter(
                NutritionLog.user_id.in_(eligible_ids),
                NutritionLog.date <= value.end_date,
            )
            .distinct()
            .all()
        )
    rows = _event_rows(
        db,
        value,
        {
            "lab_insights_viewed",
            "insight_refreshed",
            "insight_impression",
            "insight_opened",
            "evidence_expanded",
            "action_opened",
            "generation_failed",
            "lab_insight_generated",
            "recommendation_interacted",
        },
    )
    views = [row for row in rows if row.event_name == "lab_insights_viewed"]
    viewers = {row.user_id for row in views if row.user_id is not None}
    generated = [row for row in rows if row.event_name == "lab_insight_generated"]
    refreshed = [row for row in rows if row.event_name == "insight_refreshed"]
    impressions = [row for row in rows if row.event_name == "insight_impression"]
    opened = [row for row in rows if row.event_name == "insight_opened"]
    evidence_expanded = [row for row in rows if row.event_name == "evidence_expanded"]
    action_opened = [row for row in rows if row.event_name == "action_opened"]
    failed = [row for row in rows if row.event_name == "generation_failed"]
    categories: Counter[str] = Counter()
    for row in impressions:
        category = (row.properties or {}).get("detector_id")
        if isinstance(category, str) and _SAFE_STRUCTURED_LABEL.fullmatch(category):
            categories[category] += 1
    return {
        "date_range": value.payload(),
        "eligible_users": len(eligible_ids),
        "eligible_users_with_core_history": len(eligible_ids & history_ids),
        "users_lacking_core_history": len(eligible_ids - history_ids),
        "lab_insights_users": len(viewers),
        "lab_insights_views": len(views),
        "insights_generated": len(generated),
        "insights_refreshed": len(refreshed),
        "insight_impressions": len(impressions),
        "insight_opens": len(opened),
        "evidence_expansions": len(evidence_expanded),
        "action_opens": len(action_opened),
        "generation_failures": len(failed),
        "average_views_per_viewer": round(len(views) / len(viewers), 2) if viewers else 0.0,
        "recommendation_interactions": len(opened) + len(action_opened) + sum(
            1 for row in rows if row.event_name == "recommendation_interacted"
        ),
        "common_categories": [
            {"key": key, "label": key, "count": count}
            for key, count in categories.most_common(10)
        ],
        "data_quality": [
            "Lab V2 is available immediately; domain confidence is determined per finding.",
            "Core history means at least one completed workout or nutrition entry; complete-day coverage is reported separately in Lab.",
            "Legacy generated counts are retained only for old clients; refresh, impression, open, evidence, action, and failure events use V2 semantics.",
            "No recommendation text, evidence values, or raw health data is aggregated.",
        ],
    }


def event_page(
    db: Session,
    value: DateRange,
    *,
    page: int,
    page_size: int,
    event_name: str | None,
    user_id: int | None,
) -> dict[str, Any]:
    query = db.query(AnalyticsEvent).filter(
        AnalyticsEvent.occurred_at >= value.start_at,
        AnalyticsEvent.occurred_at < value.end_at,
    )
    if event_name:
        query = query.filter(AnalyticsEvent.event_name == event_name)
    if user_id is not None:
        query = query.filter(AnalyticsEvent.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(AnalyticsEvent.occurred_at.desc(), AnalyticsEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "event_name": row.event_name,
                "user_id": row.user_id,
                "actor": f"user:{row.user_id}" if row.user_id is not None else "anonymous",
                "occurred_at": row.occurred_at,
                "platform": row.platform,
                "app_version": row.app_version,
                "properties": safe_stored_event_properties(
                    row.event_name, row.properties
                ),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }


def error_analytics(db: Session, value: DateRange) -> dict[str, Any]:
    rows = _event_rows(db, value, FAILURE_EVENT_NAMES)
    aggregates: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in rows:
        error_code = (row.properties or {}).get("error_code")
        if not isinstance(error_code, str):
            error_code = None
        key = (row.event_name, error_code)
        bucket = aggregates.setdefault(
            key,
            {"count": 0, "users": set(), "last": row.occurred_at},
        )
        bucket["count"] += 1
        if row.user_id is not None:
            bucket["users"].add(row.user_id)
        if _as_utc(row.occurred_at) > _as_utc(bucket["last"]):
            bucket["last"] = row.occurred_at
    items = [
        {
            "event_name": name,
            "error_code": code,
            "count": data["count"],
            "unique_users": len(data["users"]),
            "last_occurred": data["last"],
        }
        for (name, code), data in aggregates.items()
    ]
    items.sort(key=lambda item: (item["count"], item["last_occurred"]), reverse=True)
    return {"date_range": value.payload(), "items": items}
