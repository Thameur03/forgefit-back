import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os

load_dotenv()

MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@forgefit.com")
MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


def _send_email(to_email: str, subject: str, body: str) -> None:
    """Send an email using SMTP. Falls back to printing to terminal on failure."""
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
        print(f"[EMAIL] Sent '{subject}' to {to_email}")
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send email to {to_email}: {e}")
        # Fallback: print code to terminal for development
        if DEBUG:
            print(f"[DEV] Email content for {to_email}: {body}")


def send_verification_email(email: str, code: str) -> None:
    """Send email verification code."""
    subject = "ForgeFit - Verify your email"
    body = f"Your verification code is: {code}. Expires in 15 minutes."
    if DEBUG:
        print(f"[DEV] Verification code for {email}: {code}")
    _send_email(email, subject, body)


def send_password_reset_email(email: str, code: str) -> None:
    """Send password reset code."""
    subject = "ForgeFit - Password Reset"
    body = f"Your password reset code is: {code}. Expires in 15 minutes."
    if DEBUG:
        print(f"[DEV] Password reset code for {email}: {code}")
    _send_email(email, subject, body)
