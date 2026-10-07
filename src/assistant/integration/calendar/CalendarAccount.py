"""The calendar account contract: one calendar source (an iCloud CalDAV login, a Google account)
holding several calendars.

An account names its calendars, answers the events of some of them over a window, creates an
event in one, and says which calendars the prompt always shows and which one takes an event that
names none. The vobject or JSON parsing is the backend's; what crosses the contract is `Event`."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from .Event import Event


@dataclass(frozen=True)
class CalendarInfo:
    name: str
    # False for a Reminders list, which iCloud serves next to the calendars: searched, never named.
    holds_events: bool = True


def split_names(configured) -> list[str]:
    """A list of calendar names from a config value: a list, a single string, or a list whose
    item is itself "Home, Family" (config_set stores such a string whole, and an indexed write
    later promotes it to a one-item list). Anything else is no names."""
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, (list, tuple)):
        return []
    return [name.strip() for item in configured for name in str(item).split(",") if name.strip()]


class CalendarAccount(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def service(self) -> str:
        """The service the account is at (CalDAV, Google), shown beside its name in the prompt."""
        return ""

    @property
    def shown_calendars(self) -> list[str]:
        """The calendars whose events the prompt always carries; empty means all of them."""
        return []

    @property
    def default_calendar(self) -> str:
        """Where an event that names no calendar goes."""
        return ""

    @abstractmethod
    def calendars(self) -> list[CalendarInfo]:
        """Every calendar of the account; may raise."""
        ...

    @abstractmethod
    def events(self, calendar_names: list[str], start: date, end: date) -> list[Event]:
        """The events of the named calendars between the two dates; may raise."""
        ...

    @abstractmethod
    def create_event(self, calendar_name: str, event: Event) -> None:
        """Raises ValueError when the account has no such calendar."""
        ...

    @abstractmethod
    def check(self) -> bool:
        """The health check: signs in and reads something; may raise."""
        ...
