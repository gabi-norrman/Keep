from typing import List

from .EmailProvider import EmailProvider
from common.ConfigManager import ConfigManager
from ...Integration import Integration


class ICloudEmailIntegration(Integration, EmailProvider):
    """Provides IMAP access to the iCloud mail account and rate-limited SMTP sending."""
    sensitive = True

    IMAP_HOST = "imap.mail.me.com"
    IMAP_FOLDER = "INBOX"
    SMTP_HOST = "smtp.mail.me.com"
    # iCloud's SMTP server does not file what it sends; the copy goes here over IMAP.
    SENT_FOLDER = "Sent Messages"
    AUTH_HELP = "regenerate the app-specific password at appleid.apple.com"

    def __init__(self):
        Integration.__init__(self)
        EmailProvider.__init__(self)

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


ICloudEmailAccess = ICloudEmailIntegration
