import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
from typing import Union, List
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("EmailService")


def send_email_alert(subject: str, body: str, to_emails: Union[str, List[str]] = None):
    """Sends an email to one or multiple recipients."""

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp-relay.brevo.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

    # Normalize recipient list
    if not to_emails:
        recipients = [DEFAULT_ADMIN_EMAIL]
    elif isinstance(to_emails, str):
        recipients = [to_emails]
    else:
        recipients = to_emails

    # Remove empty/duplicate emails
    recipients = list(set([r for r in recipients if r]))

    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("⚠️ SMTP credentials missing. Real email skipped.")
        return

    if not recipients:
        logger.warning("⚠️ No recipients specified.")
        return

    try:
        # Connect to SMTP server
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()

        # Login using Brevo SMTP credentials
        server.login(SMTP_USER, SMTP_PASSWORD)

        for recipient in recipients:
            msg = MIMEMultipart()
            msg["From"] = f"HyNa <{DEFAULT_ADMIN_EMAIL}>"
            msg["To"] = recipient
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "plain"))

            # Send using verified sender
            server.sendmail(
                DEFAULT_ADMIN_EMAIL,
                recipient,
                msg.as_string()
            )

            logger.info(f"✅ Real email successfully delivered to {recipient}")

        server.quit()

    except Exception as e:
        logger.error(f"❌ Failed to send real email: {e}")
