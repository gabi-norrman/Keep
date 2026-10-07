"""The contract an integration implements to bring mail accounts: `mail_accounts()`.

An integration is a service (IMAP today, Google and Microsoft later) the user has one or more
accounts at; `MailAccess` gathers the accounts of every enabled and configured MailSource through
the IntegrationMonitor, so a folder never has to be imported by name."""
from abc import ABC, abstractmethod

from .MailAccount import MailAccount


class MailSource(ABC):
    @abstractmethod
    def mail_accounts(self) -> list[MailAccount]:
        """The accounts this integration holds, in config order; built once, rebuilt on restart."""
        ...
