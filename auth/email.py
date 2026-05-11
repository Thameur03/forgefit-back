import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os

load_dotenv()

logger = logging.getLogger(__name__)

MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@forgefit.com")
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "AthleteLab")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


def _can_send_email() -> bool:
    """Check if SMTP credentials are configured."""
    return bool(MAIL_USERNAME and MAIL_PASSWORD and MAIL_PASSWORD != "your_app_password")


def _send_email(to_email: str, subject: str, body: str) -> None:
    """Send an email using SMTP. Falls back to logging in DEBUG mode."""
    if not _can_send_email():
        if DEBUG:
            logger.warning(
                "SMTP not configured — email NOT sent. Subject: '%s', To: %s, Body: %s",
                subject, to_email, body,
            )
        else:
            logger.warning("SMTP not configured — email NOT sent to %s", to_email)
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_FROM, to_email, msg.as_string())
        logger.info("Sent '%s' to %s", subject, to_email)
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        if DEBUG:
            logger.debug("Email content for %s: %s", to_email, body)


def send_verification_email(email: str, code: str) -> None:
    """Send email verification OTP."""
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
