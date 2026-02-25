"""Gmail sender — sends the Weekly Email Digest via SMTP using an App Password.

Setup (one-time):
  1. Enable 2-Factor Authentication on your Google account
  2. Go to myaccount.google.com/apppasswords
  3. Create an App Password (name it "Meal Planner")
  4. Add to .env:
       GMAIL_SENDER=you@gmail.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   (16-char App Password)
       GMAIL_RECIPIENT=you@gmail.com            (defaults to sender if not set)

Usage:
    from src.io.gmail_sender import send_digest
    ok = send_digest(subject="Week 9 Meal Plan", body_md="...", to="you@gmail.com")
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
    except ImportError:
        pass


def send_digest(subject: str, body_md: str, to: Optional[str] = None) -> bool:
    """Send the weekly digest email via Gmail SMTP.

    Args:
        subject:  Email subject line.
        body_md:  Plain-text / markdown body (sent as plain text).
        to:       Recipient email. Falls back to GMAIL_RECIPIENT, then GMAIL_SENDER.

    Returns:
        True on success, False on failure (never raises).
    """
    _load_env()

    sender = os.environ.get("GMAIL_SENDER", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = to or os.environ.get("GMAIL_RECIPIENT", "").strip() or sender

    if not sender:
        logger.warning(
            "GMAIL_SENDER not set. Add to .env:\n"
            "  GMAIL_SENDER=you@gmail.com\n"
            "  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx\n"
            "  Get App Password at: myaccount.google.com/apppasswords"
        )
        return False

    if not app_password:
        logger.warning(
            "GMAIL_APP_PASSWORD not set. "
            "Generate one at: myaccount.google.com/apppasswords"
        )
        return False

    if not recipient:
        recipient = sender

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body_md, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(sender, app_password)
            smtp.sendmail(sender, recipient, msg.as_string())
        logger.info("Email sent → %s", recipient)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.warning(
            "Gmail auth failed. Check GMAIL_SENDER and GMAIL_APP_PASSWORD.\n"
            "Note: App Passwords require 2FA to be enabled on your Google account."
        )
        return False
    except Exception as exc:
        logger.warning("Gmail send failed: %s", exc)
        return False


def is_configured() -> bool:
    """Return True if Gmail credentials are present in the environment."""
    _load_env()
    return bool(
        os.environ.get("GMAIL_SENDER", "").strip()
        and os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    )
