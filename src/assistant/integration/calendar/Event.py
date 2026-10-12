"""A calendar event as the assistant sees it, and the one renderer of the prompt's event lines.

A backend hands over tz-aware datetimes (an all-day event as the midnight-UTC datetimes of its
days, `all_day` set); `render` sorts the events, keeps those touching the window and writes one
line each in the home timezone — the text of the CALENDAR EVENTS COMING WEEK section and of
`load_calendar`. `covers` says whether an event falls on a home-timezone date, the way
`delete_event` / `move_event` name one. With `tag_calendars` each line names its calendar in brackets after the time,
which the section asks for when more than one calendar is shown.

`id` is the backend's handle on the event (a CalDAV UID, a Google or Graph event id), opaque to
everything but the account that read it: `delete_event` and `move_event` hand the event back
with it. `recurring` marks an occurrence of a series or the series itself, which a backend may
refuse to change. Neither is rendered."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytz

from common.ConfigManager import config
from common.logging import trace

NO_EVENTS = "(No upcoming events)"


@dataclass(frozen=True)
class Event:
    start: datetime
    end: datetime
    title: str
    calendar: str = ""
    account: str = ""
    all_day: bool = False
    id: str = ""
    recurring: bool = False


def render(events: list[Event], start_date: date, end_date: date, tag_calendars: bool = False) -> str:
    """The events that touch [start_date, end_date] (midnight UTC to midnight UTC, both ends
    inclusive), in start order, one line each: `YYYY-MM-DD HH:MM - HH:MM: title` for a same-day
    event, both dates written out otherwise, an all-day event as its date (or dates) with no
    times. NO_EVENTS when none."""
    local_tz = pytz.timezone(config.get_home_timezone())
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=pytz.UTC)
    end_dt = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=pytz.UTC)

    lines = []
    for event in sorted(events, key=lambda e: e.start):
        if start_dt <= event.start <= end_dt or start_dt <= event.end <= end_dt:
            tag = f" [{event.calendar}]" if tag_calendars and event.calendar else ""
            title = event.title.replace("\n", " ")
            lines.append(f"{when(event, local_tz)}{tag}: {title}")
    if not lines:
        trace(f"Calendar: none of {len(events)} event(s) fall between {start_date} and {end_date}")
    return "\n".join(lines) if lines else NO_EVENTS


def covers(event: Event, day: date, local_tz) -> bool:
    """Whether the event touches `day`: an all-day event on any of its days, a timed one on the
    local date of its start or its end."""
    if event.all_day:
        last = max((event.end - timedelta(days=1)).date(), event.start.date())
        return event.start.date() <= day <= last
    return event.start.astimezone(local_tz).date() <= day <= event.end.astimezone(local_tz).date()


def when(event: Event, local_tz) -> str:
    """The time part of an event's line: `YYYY-MM-DD HH:MM - HH:MM` in `local_tz`, both dates
    written out when the event crosses midnight, the date (or dates) alone for an all-day one."""
    if event.all_day:
        # The backend's DTEND of an all-day event is the midnight after its last day.
        first, last = event.start.date(), (event.end - timedelta(days=1)).date()
        if last <= first:
            return first.isoformat()
        return f"{first.isoformat()} - {last.isoformat()}"
    local_start = event.start.astimezone(local_tz)
    local_end = event.end.astimezone(local_tz)
    if local_start.date() == local_end.date():
        return f"{local_start.strftime('%Y-%m-%d')} {local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')}"
    return f"{local_start.strftime('%Y-%m-%d %H:%M')} - {local_end.strftime('%Y-%m-%d %H:%M')}"
