"""Creator transactional email endpoints use the existing protected sender only."""

import os
from unittest.mock import patch

os.environ.setdefault("INTERNAL_EMAIL_SECRET", "test-internal-secret")
from tests.support import client  # noqa: E402

AUTH = {"Authorization": "Bearer test-internal-secret"}


def test_creator_receipt_calls_existing_sender(monkeypatch):
    import routers.internal as internal
    monkeypatch.setattr(internal, "_INTERNAL_SECRET", "test-internal-secret")
    with patch("routers.internal.send_creator_request_received_email", return_value=True) as send:
        response = client.post("/internal/email/creator-request-received", json={"email": "creator@example.com"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"sent": True}
    send.assert_called_once_with("creator@example.com")


def test_creator_receipt_failure_is_reported_without_public_error(monkeypatch):
    import routers.internal as internal
    monkeypatch.setattr(internal, "_INTERNAL_SECRET", "test-internal-secret")
    with patch("routers.internal.send_creator_request_received_email", return_value=False):
        response = client.post("/internal/email/creator-request-received", json={"email": "creator@example.com"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"sent": False}


def test_creator_email_endpoints_reject_unauthorized_calls(monkeypatch):
    import routers.internal as internal
    monkeypatch.setattr(internal, "_INTERNAL_SECRET", "test-internal-secret")
    for route in ("creator-request-received", "creator-approved"):
        response = client.post("/internal/email/" + route, json={"email": "creator@example.com", "slug": "john", "code": "JOHN"})
        assert response.status_code == 401


def test_creator_approval_sender_and_copy(monkeypatch):
    import routers.internal as internal
    from auth.email_templates import render_creator_email
    monkeypatch.setattr(internal, "_INTERNAL_SECRET", "test-internal-secret")
    with patch("routers.internal.send_creator_approved_email", return_value=True) as send:
        response = client.post("/internal/email/creator-approved", json={"email": "creator@example.com", "slug": "john", "code": "JOHN"}, headers=AUTH)
    assert response.status_code == 200
    send.assert_called_once_with("creator@example.com", "john", "JOHN")
    receipt = render_creator_email()
    assert "Submitting a request does not guarantee acceptance." in receipt.plain_text
    approved = render_creator_email(approved=True, slug="john", code="JOHN")
    assert "https://dauntra.com/?ref=john" in approved.plain_text
    assert "Apple" not in approved.plain_text
