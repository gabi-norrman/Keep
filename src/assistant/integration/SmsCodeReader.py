"""The SMS code reader contract: what the auto-2FA flows ask of whatever reads the Mac's text
messages — iMessage today. A reader is always an Integration too; iCloud asks `live()` before
requesting a code on a timer and never names a folder: a code requested while nothing reads
Messages only SMS-bombs the phone (a beta tester got one per monitor tick for 100 minutes on
2026-10-16 while the iMessage feed lay dead)."""
from abc import ABC, abstractmethod

from common.logging import trace


class SmsCodeReader(ABC):
    """What an integration that forwards the codes texted to the Mac implements."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def reads_sms_codes(self) -> bool:
        """True while the reader is live — its poll of the Mac's messages is running or about
        to, so a code Apple texts now will be seen; False when it is stopped, failed or never
        opened."""
        ...


def live() -> list[str]:
    """The names of the registered readers that are live now; [] when a texted code would go
    unread. Resolved per call, so a feed that failed or recovered since counts as it is now."""
    from assistant.integration.IntegrationMonitor import IntegrationMonitor
    names = []
    for integration in IntegrationMonitor.get_instance().of_type(SmsCodeReader):
        try:
            if integration.reads_sms_codes():
                names.append(integration.name)
            else:
                trace(f"SMS codes: {integration.name} is registered but not reading now")
        except Exception as e:
            trace(f"SMS codes: {integration.name} could not say whether it reads ({e})")
    return names
