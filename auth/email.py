"""DAUNTRA transactional email delivery.

Resend's HTTPS API is preferred. SMTP remains an optional fallback. Real
credentials are environment-only and are never included in diagnostics.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from dotenv import load_dotenv
from email_validator import EmailNotValidError, validate_email

from brand import BRAND_NAME, EMAIL_TEAM_NAME
from config import is_production

load_dotenv()
logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_TEST_RECIPIENT = os.getenv("RESEND_TEST_RECIPIENT", "").strip().lower()
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", BRAND_NAME).strip() or BRAND_NAME
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com").strip()
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_STARTTLS = os.getenv("MAIL_STARTTLS", "true").strip().lower() == "true"
MAIL_SSL_TLS = os.getenv("MAIL_SSL_TLS", "false").strip().lower() == "true"
_DEFAULT_SENDER = "onboarding@resend.dev"


def _mask_email(email: str) -> str:
    at = email.find("@")
    if at <= 0:
        return "***"
    return email[0] + "***" + email[at:]


def _valid_email_address(value: str) -> bool:
    if not value:
        return False
    try:
        validate_email(value, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def _sender_parts() -> tuple[str, str]:
    name, address = parseaddr(MAIL_FROM)
    if not address:
        return MAIL_FROM_NAME, ""
    return name.strip() or MAIL_FROM_NAME, address.strip().lower()


def _sender_header(*, sandbox: bool = False) -> str:
    if sandbox:
        return formataddr((MAIL_FROM_NAME, _DEFAULT_SENDER))
    return formataddr(_sender_parts())


def _domain_verified() -> bool:
    """Whether a valid custom sender is configured (dashboard check is manual)."""
    _, address = _sender_parts()
    return _valid_email_address(address) and address != _DEFAULT_SENDER


def _can_send_smtp() -> bool:
    sender = _sender_parts()[1] or MAIL_USERNAME
    return bool(
        MAIL_USERNAME
        and MAIL_PASSWORD
        and MAIL_PASSWORD != "your_app_password"
        and _valid_email_address(sender)
        and (not is_production() or MAIL_STARTTLS or MAIL_SSL_TLS)
    )


def email_delivery_configured() -> bool:
    if RESEND_API_KEY:
        if _domain_verified():
            return True
        if not is_production() and _valid_email_address(RESEND_TEST_RECIPIENT):
            return True
    return _can_send_smtp()


def email_configuration_issue() -> str | None:
    if email_delivery_configured():
        return None
    if (
        is_production()
        and MAIL_USERNAME
        and MAIL_PASSWORD
        and not (MAIL_STARTTLS or MAIL_SSL_TLS)
    ):
        return "Production SMTP requires STARTTLS or implicit TLS"
    if RESEND_API_KEY and is_production() and not _domain_verified():
        return "Resend is configured but production MAIL_FROM is not a valid custom-domain sender"
    if RESEND_API_KEY and not _domain_verified():
        return "Resend sandbox mode requires a valid RESEND_TEST_RECIPIENT outside production"
    return "No usable Resend or SMTP email provider is configured"


def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    if not RESEND_API_KEY:
        return False
    masked = _mask_email(to_email)
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        params: resend.Emails.SendParams = {
            "from": _sender_header(sandbox=not _domain_verified()),
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        resend.Emails.send(params)
        logger.info("[Resend] Email delivered to %s", masked)
        return True
    except Exception as exc:
        logger.error("[Resend] Delivery failed for %s (%s)", masked, type(exc).__name__)
        return False


def _send_via_smtp(to_email: str, subject: str, body: str) -> bool:
    if not _can_send_smtp():
        return False
    masked = _mask_email(to_email)
    msg = MIMEMultipart()
    msg["From"] = _sender_header() if _sender_parts()[1] else formataddr((MAIL_FROM_NAME, MAIL_USERNAME))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        if MAIL_SSL_TLS:
            with smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                server.ehlo()
                if MAIL_STARTTLS:
                    server.starttls()
                    server.ehlo()
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)
        logger.info("[SMTP] Email delivered to %s", masked)
        return True
    except Exception as exc:
        logger.error("[SMTP] Delivery failed for %s (%s)", masked, type(exc).__name__)
        return False


def _send_email(to_email: str, subject: str, body: str) -> bool:
    masked = _mask_email(to_email)
    if RESEND_API_KEY and not _domain_verified():
        if is_production():
            logger.error("[Email] Refusing Resend sandbox sender in production")
        elif not RESEND_TEST_RECIPIENT:
            logger.warning("[Email] Sandbox recipient is not configured; delivery skipped")
        elif to_email.strip().lower() != RESEND_TEST_RECIPIENT:
            logger.info("[Email] Sandbox delivery skipped for non-test recipient %s", masked)
        else:
            return _send_via_resend(to_email, subject, body)
    elif RESEND_API_KEY and _send_via_resend(to_email, subject, body):
        return True
    if _can_send_smtp():
        return _send_via_smtp(to_email, subject, body)
    logger.error("[Email] No delivery path succeeded for %s", masked)
    return False


def send_verification_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending verification email to %s", _mask_email(email))
    return _send_email(
        email,
        f"Verify your {BRAND_NAME} account",
        f"Hello,\n\nYour {BRAND_NAME} verification code is:\n\n    {code}\n\nThis code expires in 15 minutes.\n\nIf you did not create an account, please ignore this email.\n\n— {EMAIL_TEAM_NAME}",
    )


def send_password_reset_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending password reset email to %s", _mask_email(email))
    return _send_email(
        email,
        f"Reset your {BRAND_NAME} password",
        f"Hello,\n\nYour {BRAND_NAME} password reset code is:\n\n    {code}\n\nThis code expires in 15 minutes.\n\nIf you did not request a password reset, please ignore this email.\n\n— {EMAIL_TEAM_NAME}",
    )


def send_account_deletion_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending account deletion email to %s", _mask_email(email))
    return _send_email(
        email,
        f"Confirm deletion of your {BRAND_NAME} account",
        f"Hello,\n\nA request was made to permanently delete your {BRAND_NAME} account.\n\nYour confirmation code is:\n\n    {code}\n\nThis code expires in 15 minutes. Enter it only on an official {BRAND_NAME} deletion page.\n\nIf you did not request deletion, ignore this email. Your account will not be deleted.\n\n— {EMAIL_TEAM_NAME}",
    )
