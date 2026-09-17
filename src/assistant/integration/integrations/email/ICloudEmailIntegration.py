import re
import smtplib
from email.mime.text import MIMEText
from typing import List

from .EmailProvider import EmailProvider
from common.logging import info, error
from common.ConfigManager import ConfigManager
from common.RateLimiter import RateLimiter
from ...Integration import Integration


SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 20
SEND_RATE_LIMIT_WINDOW = 60.0
SEND_RATE_LIMIT_MAX = 5
EMAIL_ADDRESS_PATTERN = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


class ICloudEmailIntegration(Integration, EmailProvider):
    """Provides IMAP access to the iCloud mail account and rate-limited SMTP sending."""

    IMAP_HOST = "imap.mail.me.com"
    IMAP_FOLDER = "INBOX"

    def __init__(self):
        Integration.__init__(self)
        EmailProvider.__init__(self)
        self._rate_limiter = RateLimiter(SEND_RATE_LIMIT_MAX, SEND_RATE_LIMIT_WINDOW)

    @property
    def name(self) -> str:
        return "iCloud Email"

    @property
    def config_keys(self) -> List[str]:
        return ["icloud_email"]

    @property
    def id_prefix(self) -> str:
        return "IC"

    def _get_credentials(self) -> tuple[str, str]:
        config = ConfigManager()
        return config.get("icloud_email.address"), config.get("icloud_email.password")

    def check_health(self) -> bool:
        from imap_tools import MailBox
        address, password = self._get_credentials()
        with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=self.IMAP_FOLDER):
            pass
        return True

    def send(self, to: str, subject: str, body: str) -> str:
        """Returns "" on success or a short error string on failure (the convention
        the LLM relies on to detect and surface send failures)."""
        recipient = (to or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not recipient:
            info("Email send skipped: empty recipient")
            return "empty recipient"
        if not subject:
            info("Email send skipped: empty subject")
            return "empty subject"
        if not body:
            info("Email send skipped: empty body")
            return "empty body"

        if not EMAIL_ADDRESS_PATTERN.match(recipient):
            info(f"Email send blocked: invalid recipient '{recipient}'")
            return f"invalid recipient: {recipient}"

        rate_err = self._rate_limiter.check()
        if rate_err:
            info(f"Email send blocked: {rate_err}")
            return rate_err

        try:
            address, password = self._get_credentials()
        except KeyError as e:
            error(f"Email send: iCloud credentials not configured: {e}")
            return "iCloud email credentials not configured"

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = address
        msg["To"] = recipient

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(address, password)
                server.sendmail(address, [recipient], msg.as_string())
        except smtplib.SMTPAuthenticationError as e:
            error(f"Email send auth failed: {e}")
            return "iCloud SMTP auth failed — regenerate the app-specific password at appleid.apple.com"
        except smtplib.SMTPRecipientsRefused as e:
            error(f"Email send: recipient refused: {e}")
            return f"recipient refused: {recipient}"
        except smtplib.SMTPException as e:
            error(f"Email send SMTP error: {e}")
            return f"SMTP error: {e}"
        except (TimeoutError, OSError) as e:
            error(f"Email send network error: {e}")
            return f"network error sending email: {e}"

        self._rate_limiter.record()
        info(f"Email sent to {recipient}: subject={subject!r}")
        return ""


ICloudEmailAccess = ICloudEmailIntegration
