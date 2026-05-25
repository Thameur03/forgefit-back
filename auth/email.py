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

def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    """Send via Resend HTTP API. Returns True on success."""
    if not RESEND_API_KEY:
        logger.info("[Resend] No RESEND_API_KEY — skipping")
        return False

    logger.info(
        "[Resend] Attempting send to=%s from=%s key_present=True",
        to_email, MAIL_FROM or "onboarding@resend.dev",
    )
    try:
        import resend  # already in requirements.txt
        resend.api_key = RESEND_API_KEY
        from_addr = MAIL_FROM if MAIL_FROM else "onboarding@resend.dev"
        params: resend.Emails.SendParams = {
            "from": f"{MAIL_FROM_NAME} <{from_addr}>",
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        response = resend.Emails.send(params)
        email_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", "?")
        logger.info("[Resend] ✓ Delivered '%s' to %s — id=%s", subject, to_email, email_id)
        return True
    except Exception as e:
        logger.error(
            "[Resend] ✗ Failed to send to %s: %s: %s",
            to_email, type(e).__name__, e,
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

def _send_email(to_email: str, subject: str, body: str) -> None:
    """
    Try Resend HTTP API first (port 443 — never blocked by cloud hosts),
    then fall back to SMTP.
    Never raises — email failure must not crash the request.
    """
    # ── Try Resend ────────────────────────────────────────
    if RESEND_API_KEY:
        if _send_via_resend(to_email, subject, body):
            return
        logger.warning("[Email] Resend failed — falling back to SMTP for %s", to_email)

    # ── Try SMTP ──────────────────────────────────────────
    if _can_send_smtp():
        if _send_via_smtp(to_email, subject, body):
            return
        logger.error(
            "[Email] Both Resend and SMTP failed for %s. "
            "Email NOT delivered. Check Render env vars and network access.",
            to_email,
        )
    elif not RESEND_API_KEY:
        logger.error(
            "[Email] No email provider configured. "
            "Set RESEND_API_KEY (recommended) or MAIL_USERNAME+MAIL_PASSWORD on Render. "
            "Email NOT sent to %s.",
            to_email,
        )


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


def send_password_reset_email(email: str, code: str) -> None:
    """Send password reset OTP."""
    logger.info("[Email] Sending password reset email to %s", email)
    subject = "Reset your AthleteLab password"
    body = (
        f"Hello,\n\n"
        f"Your AthleteLab password reset code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in 15 minutes.\n\n"
        f"If you did not request a password reset, please ignore this email.\n\n"
        f"— The AthleteLab Team"
    )
    if DEBUG:
        logger.debug(">>> DEV — Password reset code for %s: %s", email, code)
    _send_email(email, subject, body)
