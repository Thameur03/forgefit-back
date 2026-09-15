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

from brand import BRAND_NAME
from config import is_production
from auth.email_templates import EmailBodies, render_code_email, render_waitlist_welcome_email

load_dotenv()
logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_TEST_RECIPIENT = os.getenv("RESEND_TEST_RECIPIENT", "").strip().lower()
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "").strip()
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


def _support_address() -> str | None:
    """Return a safe support/Reply-To address, or omit it without failing mail."""
    if not _valid_email_address(SUPPORT_EMAIL):
        return None
    normalized = validate_email(
        SUPPORT_EMAIL, check_deliverability=False
    ).normalized.lower()
    sender_address = _sender_parts()[1]
    if normalized == sender_address or normalized == "noreply@dauntra.com":
        return None
    return normalized


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


def _send_via_resend(
    to_email: str,
    subject: str,
    plain_text_body: str,
    html_body: str | None = None,
) -> bool:
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
            "text": plain_text_body,
        }
        if html_body:
            params["html"] = html_body
        reply_to = _support_address()
        if reply_to:
            params["reply_to"] = reply_to
        resend.Emails.send(params)
        logger.info("[Resend] Email delivered to %s", masked)
        return True
    except Exception as exc:
        logger.error("[Resend] Delivery failed for %s (%s)", masked, type(exc).__name__)
        return False


def _send_via_smtp(
    to_email: str,
    subject: str,
    plain_text_body: str,
    html_body: str | None = None,
) -> bool:
    if not _can_send_smtp():
        return False
    masked = _mask_email(to_email)
    msg = MIMEMultipart("alternative")
    msg["From"] = _sender_header() if _sender_parts()[1] else formataddr((MAIL_FROM_NAME, MAIL_USERNAME))
    msg["To"] = to_email
    msg["Subject"] = subject
    reply_to = _support_address()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.attach(MIMEText(plain_text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
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


def _send_email(
    to_email: str,
    subject: str,
    plain_text_body: str,
    html_body: str | None = None,
) -> bool:
    masked = _mask_email(to_email)
    if RESEND_API_KEY and not _domain_verified():
        if is_production():
            logger.error("[Email] Refusing Resend sandbox sender in production")
        elif not RESEND_TEST_RECIPIENT:
            logger.warning("[Email] Sandbox recipient is not configured; delivery skipped")
        elif to_email.strip().lower() != RESEND_TEST_RECIPIENT:
            logger.info("[Email] Sandbox delivery skipped for non-test recipient %s", masked)
        else:
            return _send_via_resend(to_email, subject, plain_text_body, html_body)
    elif RESEND_API_KEY and _send_via_resend(
        to_email, subject, plain_text_body, html_body
    ):
        return True
    if _can_send_smtp():
        return _send_via_smtp(to_email, subject, plain_text_body, html_body)
    logger.error("[Email] No delivery path succeeded for %s", masked)
    return False


def send_verification_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending verification email to %s", _mask_email(email))
    bodies: EmailBodies = render_code_email(
        preheader=f"Your {BRAND_NAME} verification code is ready.",
        title="VERIFY YOUR EMAIL",
        intro=(
            f"Use the verification code below to finish creating your {BRAND_NAME} account.",
        ),
        code=code,
        expiration_text="This code expires in 15 minutes.",
        security_notes=(
            f"If you did not create a {BRAND_NAME} account, you can safely ignore this email.",
        ),
        support_email=_support_address(),
    )
    return _send_email(
        email,
        f"Verify your {BRAND_NAME} account",
        bodies.plain_text,
        bodies.html,
    )


def send_password_reset_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending password reset email to %s", _mask_email(email))
    bodies: EmailBodies = render_code_email(
        preheader=f"Use this code to reset your {BRAND_NAME} password.",
        title="RESET YOUR PASSWORD",
        intro=(
            f"We received a request to reset the password for your {BRAND_NAME} account.",
        ),
        code=code,
        expiration_text="This code expires in 15 minutes.",
        security_notes=(
            "If you did not request a password reset, you can ignore this email and your password will remain unchanged.",
        ),
        support_email=_support_address(),
        support_intro="If you believe someone else is trying to access your account, contact:",
    )
    return _send_email(
        email,
        f"Reset your {BRAND_NAME} password",
        bodies.plain_text,
        bodies.html,
    )


def send_account_deletion_email(email: str, code: str) -> bool:
    logger.info("[Email] Sending account deletion email to %s", _mask_email(email))
    bodies: EmailBodies = render_code_email(
        preheader=f"Use this code to confirm your {BRAND_NAME} account deletion request.",
        title="CONFIRM ACCOUNT DELETION",
        intro=(
            f"You requested to permanently delete your {BRAND_NAME} account.",
            "Enter the code below only if you intend to continue.",
        ),
        code=code,
        expiration_text="This code expires in 15 minutes. Enter it only on an official DAUNTRA deletion page.",
        security_notes=(
            "Do not share this code with anyone.",
            "Your account will not be deleted unless this confirmation code is used.",
        ),
        support_email=_support_address(),
        support_intro="If you did not request account deletion, do not use this code and contact:",
    )
    return _send_email(
        email,
        f"Confirm deletion of your {BRAND_NAME} account",
        bodies.plain_text,
        bodies.html,
    )


def send_waitlist_welcome_email(email: str) -> bool:
    logger.info("[Email] Sending waitlist welcome email to %s", _mask_email(email))
    bodies: EmailBodies = render_waitlist_welcome_email(
        support_email=_support_address(),
    )
    return _send_email(
        email,
        "You're in \u2014 your first month is on us",
        bodies.plain_text,
        bodies.html,
    )
