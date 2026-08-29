"""Safe, admin-only operational health snapshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from time import monotonic, perf_counter
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from auth.email import email_delivery_configured
from config import get_app_env
from models.operational_event import OperationalEvent
from services.operational_counters import get as get_counter


PROCESS_STARTED_AT = datetime.now(timezone.utc)
PROCESS_STARTED_MONOTONIC = monotonic()


def _configured(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and not value.lower().startswith(("your_", "replace_")))


def _failure_count(db: Session, *, category: str | None = None, event_name: str | None = None) -> int:
    query = db.query(func.count(OperationalEvent.id)).filter(
        OperationalEvent.status == "failed",
        OperationalEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
    )
    if category:
        query = query.filter(OperationalEvent.category == category)
    if event_name:
        query = query.filter(OperationalEvent.event_name == event_name)
    return query.scalar() or 0


def system_health(db: Session) -> dict[str, Any]:
    started = perf_counter()
    db.execute(text("SELECT 1")).scalar()
    latency_ms = round((perf_counter() - started) * 1000, 2)
    try:
        migration_revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        db.rollback()
        migration_revision = "unavailable"
    now = datetime.now(timezone.utc)
    recent_rows = (
        db.query(OperationalEvent)
        .filter(OperationalEvent.status == "failed")
        .order_by(OperationalEvent.created_at.desc(), OperationalEvent.id.desc())
        .limit(20)
        .all()
    )
    build = (
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("BUILD_ID")
        or os.getenv("GIT_COMMIT")
        or "unknown"
    )
    if build != "unknown":
        build = build[:12]
    email_failures = _failure_count(db, category="email")
    return {
        "generated_at": now,
        "application": {
            "version": "1.0.0",
            "build": build,
            "environment": get_app_env(),
            "migration_revision": migration_revision,
            "uptime_seconds": max(0, int(monotonic() - PROCESS_STARTED_MONOTONIC)),
            "started_at": PROCESS_STARTED_AT.isoformat(),
        },
        "database": {
            "status": "healthy",
            "query_latency_ms": latency_ms,
        },
        "email": {
            "configured": email_delivery_configured(),
            "status": "degraded" if email_failures else ("configured" if email_delivery_configured() else "not_configured"),
            "recent_failures": email_failures,
        },
        "external_services": {
            "usda": {
                "configured": _configured("USDA_API_KEY"),
                "status": "configured" if _configured("USDA_API_KEY") else "not_configured",
                "recent_failures": _failure_count(db, event_name="usda_request"),
            },
            "exercise_db": {
                "configured": bool(os.getenv("EXERCISEDB_URL", "").strip()),
                "status": (
                    "configured"
                    if bool(os.getenv("EXERCISEDB_URL", "").strip())
                    else "not_configured"
                ),
                "recent_failures": _failure_count(db, event_name="exercise_db_request"),
            },
            "sentry": {
                "configured": _configured("SENTRY_DSN"),
                "status": "configured" if _configured("SENTRY_DSN") else "not_configured",
                "recent_failures": 0,
            },
            "posthog": {
                "configured": _configured("POSTHOG_API_KEY"),
                "status": "configured" if _configured("POSTHOG_API_KEY") else "not_configured",
                "recent_failures": 0,
            },
        },
        "api": {
            "recent_4xx": None,
            "recent_5xx": None,
            "analytics_ingest_rejections": get_counter("analytics_ingest_rejected"),
            "analytics_ingest_rejection_scope": "current backend process lifetime",
            "limitation": "General request-status telemetry is not persisted; Sentry/infrastructure logs remain the source for 4xx/5xx trends.",
        },
        "recent_failures": [
            {
                "category": row.category,
                "event_name": row.event_name,
                "error_code": row.error_code,
                "created_at": row.created_at,
            }
            for row in recent_rows
        ],
    }
