"""Cryptographically secure one-time-code helpers."""

import secrets
import string
from datetime import datetime, timezone


def generate_numeric_otp(length: int = 6) -> str:
    if length <= 0:
        raise ValueError("OTP length must be positive")
    return "".join(secrets.choice(string.digits) for _ in range(length))


def otp_has_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    normalized = expires_at
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > normalized
