"""Environment-aware, security-sensitive application configuration."""

import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_VALID_APP_ENVS = {"development", "test", "staging", "production"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_DEV_CORS = ["http://localhost:3000", "http://localhost:8080"]
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def get_app_env() -> str:
    value = os.getenv("APP_ENV", "development").strip().lower()
    if value not in _VALID_APP_ENVS:
        raise RuntimeError(
            "APP_ENV must be one of: " + ", ".join(sorted(_VALID_APP_ENVS))
        )
    return value


def is_production() -> bool:
    return get_app_env() == "production"


def env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise RuntimeError(
        f"{name} must be a boolean (true/false, yes/no, on/off, or 1/0)"
    )


def require_email_verification() -> bool:
    configured = env_bool("REQUIRE_EMAIL_VERIFICATION", default=True)
    if is_production() and not configured:
        logger.error(
            "[Auth] Ignoring REQUIRE_EMAIL_VERIFICATION=false in production"
        )
        return True
    return configured


def auto_create_tables_enabled() -> bool:
    return env_bool("AUTO_CREATE_TABLES", default=not is_production())


def normalize_origin(origin: str) -> str:
    value = origin.strip()
    if not value or value == "*":
        raise ValueError("origin must be non-empty and cannot be a wildcard")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("origin must be an absolute http(s) origin")
    if parsed.username or parsed.password:
        raise ValueError("origin must not contain user information")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("origin must not contain a path, query, or fragment")
    if not parsed.hostname:
        raise ValueError("origin hostname is missing")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("origin port is invalid") from exc
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme.lower()}://{authority}"


def parse_cors_origins(
    raw: str | None = None, *, app_env: str | None = None
) -> list[str]:
    environment = app_env or get_app_env()
    if environment not in _VALID_APP_ENVS:
        raise RuntimeError("Invalid application environment for CORS parsing")
    configured = os.getenv("CORS_ORIGINS") if raw is None else raw
    if configured is None:
        if environment in {"development", "test"}:
            return list(_DEV_CORS)
        logger.warning(
            "[CORS] CORS_ORIGINS is unset; browser cross-origin access is disabled"
        )
        return []
    if not configured.strip():
        logger.warning(
            "[CORS] CORS_ORIGINS is empty; browser cross-origin access is disabled"
        )
        return []
    pieces = configured.split(",")
    if any(not item.strip() for item in pieces):
        raise RuntimeError("CORS_ORIGINS contains an empty origin entry")
    result: list[str] = []
    for item in pieces:
        try:
            origin = normalize_origin(item)
        except ValueError as exc:
            logger.error("[CORS] Invalid CORS_ORIGINS configuration")
            raise RuntimeError(f"Invalid CORS_ORIGINS entry: {exc}") from exc
        parsed = urlsplit(origin)
        if environment in {"staging", "production"}:
            if parsed.hostname in _LOCAL_HOSTS:
                raise RuntimeError(
                    "CORS_ORIGINS cannot contain localhost in staging/production"
                )
            if parsed.scheme != "https":
                raise RuntimeError(
                    "CORS_ORIGINS entries must use HTTPS in staging/production"
                )
        if origin not in result:
            result.append(origin)
    return result
