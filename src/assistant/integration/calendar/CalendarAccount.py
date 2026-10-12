"""The calendar account contract: one calendar source (an iCloud CalDAV login, a Google account)
holding several calendars.

An account names its calendars, answers the events of some of them over a window, creates an
event in one, deletes or moves one it read earlier, and says which calendars the prompt always
shows and which one takes an event that names none. The vobject or JSON parsing is the backend's;
what crosses the contract is `Event` — its `id` is the account's own handle, so an event read by
one account is deleted or moved by that account alone."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

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

    def listed_name(self, alias: str) -> Optional[str]:
        """The name `calendars()` lists for an alias the account understands besides the listed
        names (Google's "primary" and the account address for its own calendar), or None when
        `alias` is no alias. Asked only after the listed names were matched."""
        return None

    @abstractmethod
    def events(self, calendar_names: list[str], start: date, end: date) -> list[Event]:
        """The events of the named calendars between the two dates; may raise."""
        ...

    @abstractmethod
    def create_event(self, calendar_name: str, event: Event) -> None:
        """Raises ValueError when the account has no such calendar."""
        ...

    def delete_event(self, calendar_name: str, event: Event) -> None:
        """Deletes an event `events()` returned; raises ValueError when the account cannot
        (no such event any more, a recurring one the backend will not touch)."""
        raise ValueError(f"deleting an event is not supported by the {self.service or 'account'} account {self.name}")

    def move_event(self, calendar_name: str, event: Event, start: datetime, end: datetime, all_day: bool) -> None:
        """Gives an event `events()` returned the new bounds — tz-aware datetimes, or with
        `all_day` the midnight-UTC datetimes of its first day and the day after its last — and
        raises ValueError when the account cannot."""
        raise ValueError(f"moving an event is not supported by the {self.service or 'account'} account {self.name}")

    @abstractmethod
    def check(self) -> bool:
        """The health check: signs in and reads something; may raise."""
        ...
