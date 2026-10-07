from typing import List

from assistant.integration.Integration import Integration
from assistant.integration.mail.ImapMailAccount import ImapMailAccount
from assistant.integration.mail.MailAccount import MailAccount
from assistant.integration.mail.MailSource import MailSource
from common.ConfigManager import ConfigManager


class ICloudEmailIntegration(Integration, MailSource):
    """An iCloud mailbox.

    Gives Keep the iCloud mail account: it searches and reads the mail and sends a message when
    asked.

    A MailSource holding one ImapMailAccount built from the `icloud` preset and the
    `icloud_email` section."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        config = ConfigManager.get_instance()
        self._account = ImapMailAccount(
            "iCloud Email", "IC",
            credentials=lambda: (config.get("icloud_email.address"), config.get("icloud_email.password")),
            preset="icloud")

    @property
    def name(self) -> str:
        return "iCloud Email"

    @property
    def config_keys(self) -> List[str]:
        return ["icloud_email"]

    def mail_accounts(self) -> list[MailAccount]:
        return [self._account]

    def check_health(self) -> bool:
        return self._account.check()
