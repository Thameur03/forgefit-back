# Jugurtha Fit admin analytics definitions

This document is the contract for numbers shown in the production admin panel.
The FastAPI database is the source of truth for core business analytics. PostHog
is a secondary debugging/product-exploration destination and is not queried by
the admin dashboard.

## Shared date and population rules

- Calendar dates are UTC. API `start_date` and `end_date` are inclusive dates;
  timestamp queries use `[start_date 00:00, end_date + 1 day 00:00)`.
- Unless a metric explicitly says it is a current snapshot, it uses the selected
  date range. Presets are Today, 7 days, 30 days, 90 days, and All time.
- Product-user metrics include accounts whose current role is `user`. Admin and
  superadmin accounts are excluded.
- Previous-period comparisons use the immediately preceding range of equal
  length. All-time values are not given a previous-period comparison.
- Deleted accounts cannot be reconstructed in historical user totals.

## Meaningful activity

A user is meaningfully active on a UTC calendar day when at least one of these
sources records activity on that day:

1. A persisted workout whose `completed_at` is not null.
2. A persisted nutrition-log entry.
3. One of these authenticated canonical events: `workout_completed`,
   `meal_logged`, `program_created`, `program_activated`, `program_completed`,
   `program_changed`, `workout_scheduled`, `scheduled_workout_completed`,
   `scheduled_workout_cancelled`, `personal_record_achieved`,
   `lab_insight_generated`, or `recommendation_interacted`.

`app_opened`, `session_started`, login, logout, and passive screen views do not
make a user active. Distinct user/day pairs are de-duplicated across sources.

## Canonical event ownership

The canonical taxonomy is defined in `services/analytics_events.py`. It covers
session/app open, the signup steps and funnel, login/logout, workout completion
and failures, nutrition logging/search/barcode, program changes, scheduling,
statistics, personal records, and Lab Insights interactions. Every accepted
event name maps to exactly one server-derived category and an event-specific
property allowlist.

`signup_completed`, `email_verification_completed`, `onboarding_completed`, and
`logout_completed` are backend-owned milestones committed with the underlying
account operation. Generic public or authenticated analytics ingestion rejects
attempts to fabricate them. The Flutter calls for those milestones provide only
PostHog/breadcrumb parity and do not duplicate the first-party database row.

Released legacy names are normalized before storage: `sign_up_completed` to
`signup_completed`, `workout_logged` to `workout_completed`, and the bounded
aliases listed in the taxonomy module. New client code emits only canonical
names. Unknown names are rejected rather than silently creating divergent
metrics.

## Executive metrics

### Total users

- Numerator: all current accounts with role `user`.
- Denominator: none.
- Source: `users`.
- Semantics: current snapshot, not a historical as-of count.

### New users

- Numerator: role-`user` accounts created in the selected range.
- Denominator: none.
- Source: `users.created_at`.

### Verified percentage

- Numerator: current role-`user` accounts with `is_verified = true`.
- Denominator: all current role-`user` accounts.
- Source: `users`.
- Semantics: current snapshot. It is not the selected signup cohort's
  verification conversion.

### DAU

- Numerator: distinct meaningful active users on the selected range's end date.
- Denominator: none.
- Source: meaningful-activity sources above.

### WAU

- Numerator: distinct meaningful active users in the trailing seven UTC dates
  ending on the selected range's end date.
- Denominator: none.
- Source: meaningful-activity sources above.

### MAU

- Numerator: distinct meaningful active users in the trailing 30 UTC dates
  ending on the selected range's end date.
- Denominator: none.
- Source: meaningful-activity sources above.

### Stickiness

- DAU/MAU: `DAU / MAU * 100`.
- WAU/MAU: `WAU / MAU * 100`.
- A zero MAU denominator is displayed as 0%.

### Activated users and activation rate

- Cohort/denominator: role-`user` accounts created in the selected range.
- Numerator: cohort members who, by the end of that range, are verified
  (`verified_at` before the exclusive range end), have an
  `onboarding_completed` event, and have at least one persisted completed
  workout or nutrition-log entry.
- Activated users: numerator count.
- Activation rate: `activated users / signup cohort * 100`.
- Known limitation: `onboarding_completed` is complete only from the server-side
  signup instrumentation rollout onward. Historical activation is therefore
  flagged as not fully trustworthy.

### D1, D7, D14, and D30 retention

- Cohort: role-`user` accounts created in the selected date range.
- Eligible denominator for Dn: cohort members whose signup date plus `n` UTC
  calendar days is on or before the report's `end_date`.
- Retained numerator for Dn: eligible members with meaningful activity on
  exactly signup UTC date + `n` days.
- Rate: `retained / eligible * 100`.
- Immature members are excluded from that Dn denominator; they are not counted
  as failures.
- Weekly cohort rows group signup dates by Monday-starting UTC week, then apply
  the same exact-day definitions.
- This is signup-cohort retention, not a rolling active-user count.

## Signup and activation funnel

The cohort is the distinct exact session/user identities that record
`signup_started` inside the selected date range. Later stages are followed
through the current UTC date, so a historical cohort may mature after its
acquisition range ends.

Stages are sequential intersections:

1. Signup Started: `signup_started`.
2. Signup Summary Viewed: `signup_summary_viewed` among stage 1 identities.
3. Signup Submit Clicked: `signup_submit_clicked` among stage 2 identities.
4. Account Created: server-recorded `signup_completed` among stage 3 identities.
5. Email Verified: stage 4 users whose current account is verified.
6. Onboarding Completed: server-recorded `onboarding_completed` among verified
   stage 5 identities.
7. Activated: stage 6 users who are verified and have a persisted completed
   workout or nutrition-log entry.
8. Returned D7: activated users old enough for D7 who have meaningful activity
   on exactly signup date + seven UTC calendar days.

Before authentication, identity is the exact high-entropy
`anonymous_id + session_id` pair. Registration/login may link only recent,
unclaimed events from that exact session to the authenticated `user_id`. A
persistent device identifier alone is never used to merge people. Every stage
count is intersected with its predecessor, so counts are monotonic. Each stage
shows conversion from the previous stage, conversion from stage 1, and drop-off.

The stage 8 denominator is mature activated users, exposed as `eligible_count`.
Historical verification/onboarding stages before instrumentation rollout cannot
be reconstructed completely.

## User growth

- New users per date: role-`user` accounts whose `created_at` falls on that UTC
  date.
- Verified new users: those new accounts whose current state is verified.
- Cumulative users: surviving role-`user` accounts created on or before that
  point. Deleted accounts are absent.

## Feature adoption

- Denominator: distinct meaningful active users in the selected range.
- Numerator: denominator members who use the named feature in the same range.
- Workout and nutrition adoption use persisted completed workouts and nutrition
  logs respectively.
- Programs, barcode, micronutrients, statistics, personal records, and Lab
  Insights use canonical events. Calendar/scheduling combines persisted schedule
  creation with canonical events.
- Core split assigns active users to workout only, nutrition only, both, or
  neither core tracker.
- Event-only feature history begins at instrumentation rollout.

## Workout analytics

- Completed workouts: persisted workouts in range with non-null `completed_at`.
- Unique workout users: distinct owners of those workouts.
- Workouts per active workout user: completed workouts divided by unique workout
  users.
- Average completed workouts/user/week: completed workouts divided by unique
  workout users and by `selected days / 7`.
- Average duration: mean positive `duration_seconds`; zero/missing duration is
  excluded and `duration_sample_size` is returned.
- Total sets: sum of stored workout-set counts on completed workouts.
- Training volume: sum of `sets * reps * non-negative weight_kg`.
- Top exercises: catalog-linked exercise rows only; free-text/custom names are
  excluded.
- Scheduled match: distinct user/date workout keys intersected with distinct
  user/date schedule keys. This is approximate because workouts do not retain a
  `scheduled_workout_id` foreign key.
- Personal records: `personal_record_achieved` event count.
- Workout shells created at session start are drafts and never count.
- Migration 008 conservatively marks legacy positive-duration workouts as
  `completion_inferred = true`; legacy zero-duration rows remain excluded. A KPI
  containing inferred rows is visibly marked as limited/not fully trustworthy.
- Program-vs-custom workout attribution is unavailable in the workout schema.

## Nutrition analytics

- Nutrition entries: persisted `nutrition_logs` rows in range.
- Meals logged: distinct `(user_id, date, meal_name)` groups. The schema has no
  separate meal entity.
- Nutrition-active users: distinct users with a persisted entry in range.
- Logging days: distinct `(user_id, date)` pairs.
- Average logging days/user: logging days divided by nutrition-active users.
- Barcode/manual/search/failure/macro/micronutrient usage: corresponding
  canonical event counts or distinct event users in range.
- Top foods: catalog/USDA `fdc_id` values only. Free-text user food names are
  never ranked or returned.

## Program analytics

- Active programs, users with/without active programs, custom programs,
  template programs, and most-used templates are current database snapshots.
- Template activations and program changes are canonical event counts in the
  selected range.
- Program rows currently lack created/activated timestamps, so historical
  snapshot trends cannot be reconstructed.

## Scheduling analytics

- Cohort/denominator: schedules created in the selected range, combining
  persisted rows with ID-correlated `workout_scheduled` events so a later
  deletion does not erase the original cohort.
- Scheduled-to-completed numerator: distinct cohort `schedule_id` values with a
  matching `scheduled_workout_completed` event through the current UTC date.
- Cancellation count uses the same exact ID correlation.
- Upcoming schedules are current/future persisted schedules not marked complete
  today.
- Completion/cancellation is incomplete before `schedule_id` instrumentation;
  the schedule table itself has no status-history columns.

## Lab Insights

- Eligible users: role-`user` accounts old enough for the product's current
  seven-calendar-day gate at the report end.
- Core-history readiness: eligible users with at least one persisted completed
  workout or nutrition entry by report end. The insight engine may require more
  history than this broad readiness indicator.
- Viewers/views/generated insights/interactions come from their canonical events
  in range.
- Categories are bounded structured category slugs. Recommendation text and raw
  AI output are never aggregated or exposed.

## Error and operational analytics

- Analytics errors aggregate only the bounded failure-event taxonomy by event
  name and sanitized `error_code`, with count, unique authenticated users, and
  last occurrence.
- General API 4xx/5xx history is not persisted by this application. Infrastructure
  logs and Sentry remain the source for those trends.
- Analytics-ingest rejections are a bounded, process-local counter and reset on
  backend restart.
- Email/external-service failures shown in operations come from privacy-safe
  operational outcome rows; secrets, payloads, email bodies, and user health data
  are never stored there.

## Acquisition and PostHog limitations

`signup_started` accepts bounded `utm_source`, `utm_medium`, `utm_campaign`,
`utm_content`, and `referrer` slugs. There is no reliable website-to-install
identity link yet, so website acquisition and in-app conversion must remain
separate until deep links or an install-referrer design is implemented. Full
landing URLs are not stored.

PostHog receives a privacy-safe secondary copy of supported client events and
screen navigation where configured. Session replay remains disabled. No PostHog
private/project-administration key is embedded in either Flutter app, and no
admin business metric depends on PostHog delivery.
