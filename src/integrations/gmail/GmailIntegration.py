from typing import List

from assistant.integration.Integration import Integration
from assistant.integration.mail.ImapMailAccount import ImapMailAccount
from assistant.integration.mail.MailAccount import MailAccount
from assistant.integration.mail.MailSource import MailSource
from common.ConfigManager import ConfigManager


class GmailIntegration(Integration, MailSource):
    """A Gmail mailbox.

    Gives Keep the Gmail account: it searches and reads the mail and sends a message when asked.

    A MailSource holding one ImapMailAccount built from the `gmail` preset and the `email`
    section; `email.smtp_host` / `email.smtp_port` override the preset's SMTP server."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        config = ConfigManager.get_instance()
        self._account = ImapMailAccount(
            "Gmail", "GM",
            credentials=lambda: (config.get("email.address"), config.get("email.password")),
            preset="gmail",
            smtp_host=config.get("email.smtp_host", ""),
            smtp_port=int(config.get("email.smtp_port", 0)))

    @property
    def name(self) -> str:
        return "Gmail"

    @property
    def config_keys(self) -> List[str]:
        return ["email"]

    def mail_accounts(self) -> list[MailAccount]:
        return [self._account]

    def check_health(self) -> bool:
        return self._account.check()
