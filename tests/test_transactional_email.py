"""Focused tests for branded transactional email rendering and delivery."""

from unittest.mock import patch

import pytest

import auth.email as email_service
from auth.email_templates import render_code_email


def _capture_message(monkeypatch, send_function, code: str):
    captured = {}

    def capture(to_email, subject, plain_text_body, html_body=None):
        captured.update(
            to=to_email,
            subject=subject,
            text=plain_text_body,
            html=html_body,
        )
        return True

    monkeypatch.setattr(email_service, "SUPPORT_EMAIL", "support@dauntra.com")
    monkeypatch.setattr(email_service, "_send_email", capture)
    assert send_function("member@example.com", code) is True
    return captured


def test_verification_email_is_branded_html_with_plain_text(monkeypatch):
    message = _capture_message(
        monkeypatch, email_service.send_verification_email, "123456"
    )

    assert message["subject"] == "Verify your DAUNTRA account"
    assert "123456" in message["html"]
    assert "123456" in message["text"]
    assert "VERIFY YOUR EMAIL" in message["html"]
    assert "DAUNTRA" in message["html"]
    assert "THE WORK, UNDERSTOOD." in message["html"]
    assert "support@dauntra.com" in message["html"]
    assert "dauntra-app-icon.png" in message["html"]


def test_password_reset_email_contains_actual_code(monkeypatch):
    message = _capture_message(
        monkeypatch, email_service.send_password_reset_email, "654321"
    )

    assert message["subject"] == "Reset your DAUNTRA password"
    assert "654321" in message["html"]
    assert "654321" in message["text"]
    assert "RESET YOUR PASSWORD" in message["html"]
    assert "password will remain unchanged" in message["html"]


def test_account_deletion_email_contains_actual_code(monkeypatch):
    message = _capture_message(
        monkeypatch, email_service.send_account_deletion_email, "987654"
    )

    assert message["subject"] == "Confirm deletion of your DAUNTRA account"
    assert "987654" in message["html"]
    assert "987654" in message["text"]
    assert "CONFIRM ACCOUNT DELETION" in message["html"]
    assert "Do not share this code" in message["html"]


def test_renderer_escapes_all_dynamic_html_values():
    message = render_code_email(
        preheader="Preview <unsafe>",
        title="TITLE & ACTION",
        intro=("Hello <script>alert(1)</script>",),
        code="12<&34",
        expiration_text='Expires after "one" use.',
        security_notes=("Ignore <this>.",),
        support_email='help&desk@example.com',
        support_intro="Contact <support>",
    )

    assert "<script>" not in message.html
    assert "Hello &lt;script&gt;alert(1)&lt;/script&gt;" in message.html
    assert "12&lt;&amp;34" in message.html
    assert "Preview &lt;unsafe&gt;" in message.html
    assert "Contact &lt;support&gt;" in message.html


def test_resend_payload_includes_html_text_reply_to_and_production_sender(
    monkeypatch,
):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(
        email_service, "MAIL_FROM", "DAUNTRA <noreply@dauntra.com>"
    )
    monkeypatch.setattr(email_service, "MAIL_FROM_NAME", "DAUNTRA")
    monkeypatch.setattr(email_service, "SUPPORT_EMAIL", "support@dauntra.com")
    monkeypatch.setattr(email_service, "is_production", lambda: True)

    with patch("resend.Emails.send") as resend_send:
        assert email_service._send_via_resend(
            "member@example.com", "Subject", "Plain body", "<p>HTML body</p>"
        )

    payload = resend_send.call_args.args[0]
    assert payload["from"] == "DAUNTRA <noreply@dauntra.com>"
    assert payload["to"] == ["member@example.com"]
    assert payload["subject"] == "Subject"
    assert payload["text"] == "Plain body"
    assert payload["html"] == "<p>HTML body</p>"
    assert payload["reply_to"] == "support@dauntra.com"


@pytest.mark.parametrize("support_email", ["", "not-an-email", "noreply@dauntra.com"])
def test_resend_omits_invalid_or_noreply_reply_to(monkeypatch, support_email):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(
        email_service, "MAIL_FROM", "DAUNTRA <noreply@dauntra.com>"
    )
    monkeypatch.setattr(email_service, "SUPPORT_EMAIL", support_email)

    with patch("resend.Emails.send") as resend_send:
        assert email_service._send_via_resend(
            "member@example.com", "Subject", "Plain body", "<p>HTML body</p>"
        )

    assert "reply_to" not in resend_send.call_args.args[0]


def test_development_sandbox_delivery_remains_recipient_restricted(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "RESEND_TEST_RECIPIENT", "dev@example.com")
    monkeypatch.setattr(email_service, "is_production", lambda: False)
    monkeypatch.setattr(email_service, "_domain_verified", lambda: False)
    with patch.object(
        email_service, "_send_via_resend", return_value=True
    ) as resend_send:
        assert email_service._send_email(
            "dev@example.com", "Subject", "Plain", "<p>HTML</p>"
        )
    resend_send.assert_called_once_with(
        "dev@example.com", "Subject", "Plain", "<p>HTML</p>"
    )


def test_production_rejects_resend_sandbox_sender(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "RESEND_TEST_RECIPIENT", "dev@example.com")
    monkeypatch.setattr(email_service, "is_production", lambda: True)
    monkeypatch.setattr(email_service, "_domain_verified", lambda: False)
    monkeypatch.setattr(email_service, "_can_send_smtp", lambda: False)
    with patch.object(email_service, "_send_via_resend") as resend_send:
        assert not email_service._send_email(
            "dev@example.com", "Subject", "Plain", "<p>HTML</p>"
        )
    resend_send.assert_not_called()


def test_resend_failure_still_falls_back_to_smtp(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(email_service, "_domain_verified", lambda: True)
    monkeypatch.setattr(email_service, "_can_send_smtp", lambda: True)

    with patch.object(
        email_service, "_send_via_resend", return_value=False
    ) as resend_send, patch.object(
        email_service, "_send_via_smtp", return_value=True
    ) as smtp_send:
        assert email_service._send_email(
            "member@example.com", "Subject", "Plain", "<p>HTML</p>"
        )

    resend_send.assert_called_once_with(
        "member@example.com", "Subject", "Plain", "<p>HTML</p>"
    )
    smtp_send.assert_called_once_with(
        "member@example.com", "Subject", "Plain", "<p>HTML</p>"
    )


def test_smtp_fallback_uses_multipart_alternative(monkeypatch):
    sent_messages = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, username, password):
            pass

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(email_service, "_can_send_smtp", lambda: True)
    monkeypatch.setattr(
        email_service, "MAIL_FROM", "DAUNTRA <noreply@dauntra.com>"
    )
    monkeypatch.setattr(email_service, "SUPPORT_EMAIL", "support@dauntra.com")
    monkeypatch.setattr(email_service, "MAIL_SSL_TLS", False)
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    assert email_service._send_via_smtp(
        "member@example.com", "Subject", "Plain body", "<p>HTML body</p>"
    )
    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message.get_content_subtype() == "alternative"
    assert message["Reply-To"] == "support@dauntra.com"
    assert [part.get_content_subtype() for part in message.get_payload()] == [
        "plain",
        "html",
    ]
