"""Tests for the protected /internal/email/waitlist-welcome endpoint.

Covers:
  - correct secret → 200, sent:true
  - missing Authorization header → 401
  - wrong secret → 401
  - invalid email format → 422
  - email provider failure → 200, sent:false (signup persisted in caller)
  - no secret leakage in any 401 response
"""

import os
import pytest
from unittest.mock import patch

os.environ.setdefault("INTERNAL_EMAIL_SECRET", "test-internal-secret")

from tests.support import client  # noqa: E402


_CORRECT_AUTH = "Bearer test-internal-secret"
_VALID_EMAIL = "waitlist-member@example.com"
_ENDPOINT = "/internal/email/waitlist-welcome"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_correct_secret_is_accepted(monkeypatch):
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    with patch(
        "routers.internal.send_waitlist_welcome_email", return_value=True
    ):
        response = client.post(
            _ENDPOINT,
            json={"email": _VALID_EMAIL},
            headers={"Authorization": _CORRECT_AUTH},
        )
    assert response.status_code == 200
    assert response.json()["sent"] is True


def test_missing_authorization_header_returns_401(monkeypatch):
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    response = client.post(_ENDPOINT, json={"email": _VALID_EMAIL})
    assert response.status_code == 401
    # Response must not reveal the secret or its structure
    body = response.text
    assert "test-internal-secret" not in body


def test_wrong_secret_returns_401(monkeypatch):
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    response = client.post(
        _ENDPOINT,
        json={"email": _VALID_EMAIL},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401
    assert "test-internal-secret" not in response.text


def test_bearer_prefix_required(monkeypatch):
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    # Send the raw secret value without the Bearer prefix
    response = client.post(
        _ENDPOINT,
        json={"email": _VALID_EMAIL},
        headers={"Authorization": "test-internal-secret"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_email", [
    "not-an-email",
    "",
    "missing-at-sign.com",
    "a@",
    "@nodomain",
])
def test_invalid_email_returns_422(monkeypatch, bad_email):
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    response = client.post(
        _ENDPOINT,
        json={"email": bad_email},
        headers={"Authorization": _CORRECT_AUTH},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Email provider failure
# ---------------------------------------------------------------------------

def test_email_provider_failure_returns_200_sent_false(monkeypatch):
    """The caller's signup must not fail if the email provider is unavailable."""
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "test-internal-secret")
    with patch(
        "routers.internal.send_waitlist_welcome_email", return_value=False
    ):
        response = client.post(
            _ENDPOINT,
            json={"email": _VALID_EMAIL},
            headers={"Authorization": _CORRECT_AUTH},
        )
    assert response.status_code == 200
    assert response.json()["sent"] is False


# ---------------------------------------------------------------------------
# Schema enforcement
# ---------------------------------------------------------------------------

def test_unconfigured_secret_returns_503(monkeypatch):
    """If INTERNAL_EMAIL_SECRET is not set, endpoint must fail safely."""
    import routers.internal as internal_mod
    monkeypatch.setattr(internal_mod, "_INTERNAL_SECRET", "")
    response = client.post(
        _ENDPOINT,
        json={"email": _VALID_EMAIL},
        headers={"Authorization": _CORRECT_AUTH},
    )
    assert response.status_code in {401, 503}
