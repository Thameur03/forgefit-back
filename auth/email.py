import smtplib
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Resend (primary) ──────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")

# ── SMTP (fallback) ───────────────────────────────────────────────────────────
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "onboarding@resend.dev")
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "AthleteLab")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


# ═══════════════════════════════════════════════════════════
# RESEND SDK (primary)
# ═══════════════════════════════════════════════════════════

def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    """Send via Resend SDK. Returns True on success."""
    if not RESEND_API_KEY:
        return False
    try:
        import resend  # noqa: PLC0415
        resend.api_key = RESEND_API_KEY
        params: resend.Emails.SendParams = {
            "from": f"{MAIL_FROM_NAME} <{MAIL_FROM}>",
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        response = resend.Emails.send(params)
        logger.info("[Resend] Sent '%s' to %s — id=%s", subject, to_email, response.get("id"))
        return True
    except Exception as e:
        logger.error("[Resend] Failed to send to %s: %s: %s", to_email, type(e).__name__, e)
        return False


# ═══════════════════════════════════════════════════════════
# SMTP (fallback)
# ═══════════════════════════════════════════════════════════

def _can_send_smtp() -> bool:
    return bool(
        MAIL_USERNAME
        and MAIL_PASSWORD
        and MAIL_PASSWORD != "your_app_password"
    )


def _send_via_smtp(to_email: str, subject: str, body: str) -> bool:
    """Send via SMTP STARTTLS. Returns True on success."""
    if not _can_send_smtp():
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=20) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_FROM, to_email, msg.as_string())
        logger.info("[SMTP] Sent '%s' to %s", subject, to_email)
        return True
    except Exception as e:
        logger.error("[SMTP] Failed to send to %s: %s", to_email, type(e).__name__)
        return False


# ═══════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════

def _send_email(to_email: str, subject: str, body: str) -> None:
    """
    Try Resend first, then fall back to SMTP.
    Never raises — email failure must not crash the request.
    """
    if RESEND_API_KEY:
        logger.info("[Email] Trying Resend for %s", to_email)
        if _send_via_resend(to_email, subject, body):
            return
        logger.warning("[Email] Resend failed, trying SMTP for %s", to_email)

    if _can_send_smtp():
        logger.info("[Email] Trying SMTP for %s", to_email)
        if _send_via_smtp(to_email, subject, body):
            return
        logger.error("[Email] SMTP also failed for %s", to_email)
    elif not RESEND_API_KEY:
        logger.warning(
            "[Email] No provider configured (RESEND_API_KEY missing, MAIL_PASSWORD placeholder). "
            "Email NOT sent to %s.",
            to_email,
        )
        if DEBUG:
            logger.debug("[Email] Subject=%r Body=%s", subject, body)


# ═══════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════

def send_verification_email(email: str, code: str) -> None:
    """Send email verification OTP."""
    logger.info("[Email] Queueing verification email for %s", email)
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
    logger.info("[Email] Queueing password reset email for %s", email)
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
