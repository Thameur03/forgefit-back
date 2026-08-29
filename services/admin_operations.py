"""Shared append-only administration and privacy-safe operations recording."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from models.admin_audit import AdminAuditEvent
from models.operational_event import OperationalEvent
from models.user import User
from database import SessionLocal


_SAFE_VALUE = re.compile(r"^[a-zA-Z0-9_.:@/+-]{1,100}$")


def _safe_metadata(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", key):
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, int) and -1_000_000_000 <= value <= 1_000_000_000:
            clean[key] = value
        elif isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
            clean[key] = value
    return clean or None


def add_admin_audit_event(
    db: Session,
    *,
    admin: User,
    action: str,
    target_type: str,
    target_id: int | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AdminAuditEvent:
    """Stage one immutable audit row in the caller's transaction."""
    event = AdminAuditEvent(
        admin_user_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=str(target_id)[:100] if target_id is not None else None,
        metadata_json=_safe_metadata(metadata),
    )
    db.add(event)
    return event


def add_operational_event(
    db: Session,
    *,
    category: str,
    event_name: str,
    status: str,
    error_code: str | None = None,
) -> OperationalEvent:
    """Stage a safe aggregate-able outcome without a user identifier."""
    event = OperationalEvent(
        category=category[:50],
        event_name=event_name[:100],
        status=status[:20],
        error_code=error_code[:64] if error_code else None,
    )
    db.add(event)
    return event


def record_operational_event(
    *,
    category: str,
    event_name: str,
    status: str,
    error_code: str | None = None,
) -> None:
    """Best-effort isolated recording for external-call exception paths."""
    db = SessionLocal()
    try:
        add_operational_event(
            db,
            category=category,
            event_name=event_name,
            status=status,
            error_code=error_code,
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
