"""
auth/email.py — Email sending for AthleteLab
Priority: Resend HTTP API → Gmail SMTP fallback
"""
import smtplib
import logging
import os
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Resend (primary) ──────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")

# ── Test-mode recipient ───────────────────────────────────────────────────────
# When the Resend sending domain is not yet verified, Resend only allows email
# delivery to the email address associated with the Resend account.
#
# Set RESEND_TEST_RECIPIENT to that address (without the value, no reset email
# is delivered in test mode).
#
# IMPORTANT: test-mode delivery fires ONLY when the requested recipient email
# exactly equals RESEND_TEST_RECIPIENT.  We never redirect another user's reset
# code to the developer's inbox.
RESEND_TEST_RECIPIENT = os.getenv("RESEND_TEST_RECIPIENT", "").strip().lower()

# Helper: True when a custom sender domain has been verified in Resend.
_DEFAULT_SENDER = "onboarding@resend.dev"
def _domain_verified() -> bool:
    return bool(MAIL_FROM) and MAIL_FROM.strip() != _DEFAULT_SENDER


# ── SMTP credentials ──────────────────────────────────────────────────────────
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "")
MAIL_FROM_NAME= os.getenv("MAIL_FROM_NAME", "AthleteLab")
MAIL_SERVER   = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "587"))
MAIL_STARTTLS = os.getenv("MAIL_STARTTLS", "true").lower() == "true"
MAIL_SSL_TLS  = os.getenv("MAIL_SSL_TLS", "false").lower() == "true"
DEBUG         = os.getenv("DEBUG", "false").lower() == "true"


# ═══════════════════════════════════════════════════════════
# RESEND HTTP API (primary — uses port 443, never blocked)
# ═══════════════════════════════════════════════════════════

def _mask_email(email: str) -> str:
    """Return a privacy-safe masked representation, e.g. 'u***@example.com'."""
    at = email.find("@")
    if at <= 0:
        return "***"
    return email[0] + "***" + email[at:]


def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    """Send via Resend HTTP API. Returns True on success."""
    if not RESEND_API_KEY:
        logger.info("[Resend] No RESEND_API_KEY — skipping")
        return False

    masked = _mask_email(to_email)
    logger.info(
        "[Resend] Attempting send to=%s domain_verified=%s",
        masked, _domain_verified(),
    )
    try:
        import resend  # already in requirements.txt
        resend.api_key = RESEND_API_KEY
        from_addr = MAIL_FROM if _domain_verified() else _DEFAULT_SENDER
        params: resend.Emails.SendParams = {
            "from": f"{MAIL_FROM_NAME} <{from_addr}>",
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        response = resend.Emails.send(params)
        email_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", "?")
        logger.info("[Resend] ✓ Delivered '%s' to %s — id=%s", subject, masked, email_id)
        return True
    except Exception as e:
        logger.error(
            "[Resend] ✗ Failed to send to %s: %s: %s",
            masked, type(e).__name__, e,
        )
        return False


# ═══════════════════════════════════════════════════════════
# SMTP (fallback)
# ═══════════════════════════════════════════════════════════

def _can_send_smtp() -> bool:
    return bool(
        MAIL_USERNAME
        and MAIL_PASSWORD
        and MAIL_PASSWORD not in ("your_app_password", "")
    )


def _send_via_smtp(to_email: str, subject: str, body: str) -> bool:
    """
    Send via SMTP.
    - Port 587 → STARTTLS (Gmail, most providers)
    - Port 465 → SSL/TLS
    Returns True on success.
    """
    if not _can_send_smtp():
        logger.warning("[SMTP] Credentials not configured — skipping")
        return False

    logger.info(
        "[SMTP] Config: host=%s port=%s starttls=%s ssl=%s "
        "username_present=%s password_present=%s from_present=%s",
        MAIL_SERVER, MAIL_PORT, MAIL_STARTTLS, MAIL_SSL_TLS,
        bool(MAIL_USERNAME), bool(MAIL_PASSWORD), bool(MAIL_FROM),
    )

    msg = MIMEMultipart()
    msg["From"]    = f"{MAIL_FROM_NAME} <{MAIL_FROM or MAIL_USERNAME}>"
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        if MAIL_SSL_TLS:
            # Port 465 — direct SSL
            logger.info("[SMTP] Connecting via SSL to %s:%s", MAIL_SERVER, MAIL_PORT)
            with smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)
        else:
            # Port 587 — STARTTLS (Gmail default)
            logger.info("[SMTP] Connecting via STARTTLS to %s:%s", MAIL_SERVER, MAIL_PORT)
            with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=15) as server:
                server.ehlo()
                if MAIL_STARTTLS:
                    server.starttls()
                    server.ehlo()
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(msg)

        logger.info("[SMTP] ✓ Delivered '%s' to %s", subject, to_email)
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            "[SMTP] ✗ Authentication failed for %s — "
            "Check MAIL_USERNAME and MAIL_PASSWORD (Gmail needs an App Password, not your login password). "
            "Error: %s",
            MAIL_USERNAME, e,
        )
        return False
    except smtplib.SMTPException as e:
        logger.error("[SMTP] ✗ SMTP error sending to %s: %s: %s", to_email, type(e).__name__, e)
        return False
    except OSError as e:
        logger.error(
            "[SMTP] ✗ Network/OS error sending to %s: %s: %s — "
            "This usually means the host cannot reach %s:%s. "
            "Render free tier may block outbound SMTP (ports 25/465/587). "
            "Use Resend API (port 443) instead.",
            to_email, type(e).__name__, e, MAIL_SERVER, MAIL_PORT,
        )
        return False
    except Exception as e:
        logger.error(
            "[SMTP] ✗ Unexpected error sending to %s: %s: %s\n%s",
            to_email, type(e).__name__, e, traceback.format_exc(),
        )
        return False


# ═══════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════

def _send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Try Resend HTTP API first (port 443 — never blocked by cloud hosts),
    then fall back to SMTP.

    Test-mode gate: when the Resend domain has not yet been verified, delivery
    is permitted ONLY when `to_email` exactly equals `RESEND_TEST_RECIPIENT`.
    This prevents a developer from receiving another user's reset code in their
    own inbox.

    Never raises — email failure must not crash the request.
    Returns True if the email was delivered, False otherwise.
    """
    masked = _mask_email(to_email)

    # ── Test-mode guard (unverified Resend domain) ──────────────────────────
    if RESEND_API_KEY and not _domain_verified():
        if not RESEND_TEST_RECIPIENT:
            logger.warning(
                "[Email] Test mode: RESEND_TEST_RECIPIENT not set. "
                "Cannot deliver to %s — skipping.", masked
            )
            return False
        if to_email.strip().lower() != RESEND_TEST_RECIPIENT:
            logger.info(
                "[Email] Test mode: requested recipient %s does not match "
                "RESEND_TEST_RECIPIENT — delivery skipped to prevent "
                "cross-user code exposure.", masked
            )
            return False
        logger.info(
            "[Email] Test mode: recipient matches RESEND_TEST_RECIPIENT — proceeding."
        )

    # ── Try Resend ────────────────────────────────────────────────────────
    if RESEND_API_KEY:
        if _send_via_resend(to_email, subject, body):
            return True
        logger.warning("[Email] Resend failed — falling back to SMTP for %s", masked)

    # ── Try SMTP ─────────────────────────────────────────────────────────
    # NOTE: Render free tier may block outbound SMTP ports 25/465/587.
    # Prefer the Resend API (port 443) for production.
    if _can_send_smtp():
        if _send_via_smtp(to_email, subject, body):
            return True
        logger.error(
            "[Email] Both Resend and SMTP failed for %s. "
            "Email NOT delivered. Check Render env vars and network access.",
            masked,
        )
    elif not RESEND_API_KEY:
        logger.error(
            "[Email] No email provider configured. "
            "Set RESEND_API_KEY (recommended) or MAIL_USERNAME+MAIL_PASSWORD on Render. "
            "Email NOT sent to %s.",
            masked,
        )
    return False


# ═══════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════

def send_verification_email(email: str, code: str) -> None:
    """Send email verification OTP."""
    logger.info("[Email] Sending verification email to %s", email)
    subject = "Verify your AthleteLab account"
    body = (
        f"Hello,\n\n"
        f"Your AthleteLab verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in 15 minutes.\n\n"
        f"If you did not create an account, please ignore this email.\n\n"
        f"— The AthleteLab Team"
    )
    if DEBUG:
        logger.debug(">>> DEV — Verification code for %s: %s", email, code)
    _send_email(email, subject, body)


def send_password_reset_email(email: str, code: str) -> bool:
    """Send password reset OTP. Returns True if delivery succeeded."""
    logger.info("[Email] Sending password reset email to %s", _mask_email(email))
    subject = "Reset your AthleteLab password"
    body = (
        f"Hello,\n\n"
        f"Your AthleteLab password reset code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in 15 minutes.\n\n"
        f"If you did not request a password reset, please ignore this email.\n\n"
        f"— The AthleteLab Team"
    )
    return _send_email(email, subject, body)
