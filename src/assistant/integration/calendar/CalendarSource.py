"""The contract an integration implements to bring calendar accounts: `calendar_accounts()`.

`CalendarAccess` gathers the accounts of every enabled and configured CalendarSource through
the IntegrationMonitor, the way MailAccess gathers mail accounts."""
from abc import ABC, abstractmethod

from .CalendarAccount import CalendarAccount


class CalendarSource(ABC):
    @abstractmethod
    def calendar_accounts(self) -> list[CalendarAccount]:
        """The accounts this integration holds, in config order; built once, rebuilt on restart."""
        ...
