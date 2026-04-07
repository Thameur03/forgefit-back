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
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


def _send_email(to_email: str, subject: str, body: str) -> None:
    """Send an email using SMTP. Falls back to logging on failure."""
    try:
        msg = MIMEMultipart()
        msg["From"] = MAIL_FROM
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
        # Fallback: log code for development
        if DEBUG:
            logger.debug("Email content for %s: %s", to_email, body)


def send_verification_email(email: str, code: str) -> None:
    """Send email verification code."""
    subject = "ForgeFit - Verify your email"
    body = f"Your verification code is: {code}. Expires in 15 minutes."
    if DEBUG:
        logger.debug("Verification code for %s: %s", email, code)
    _send_email(email, subject, body)


def send_password_reset_email(email: str, code: str) -> None:
    """Send password reset code."""
    subject = "ForgeFit - Password Reset"
    body = f"Your password reset code is: {code}. Expires in 15 minutes."
    if DEBUG:
        logger.debug("Password reset code for %s: %s", email, code)
    _send_email(email, subject, body)

