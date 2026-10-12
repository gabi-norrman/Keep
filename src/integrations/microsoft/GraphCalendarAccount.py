"""A CalendarAccount over Microsoft Graph: the calendars of a Microsoft 365 account, their events over a window, and a new event.

`calendarView` expands recurrences on the server and, asked with
`Prefer: outlook.timezone="<home tz>"`, answers in the home timezone; the times become
tz-aware `Event`s and an all-day event the midnight-UTC datetimes the renderer expects. A new
event is posted with the home timezone, so what the user said at 09:00 is at 09:00 in Outlook."""
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import pytz

from assistant.integration.calendar.CalendarAccount import CalendarAccount, CalendarInfo
from assistant.integration.calendar.Event import Event
from common.logging import info, trace
from integrations.microsoft.GraphClient import GraphClient, GraphError

EVENT_SELECT = "id,subject,start,end,isAllDay,type"
# The Graph event types that belong to a series: the master, an occurrence, a changed occurrence.
RECURRING_TYPES = ("seriesMaster", "occurrence", "exception")
PAGE_SIZE = 250


class GraphCalendarAccount(CalendarAccount):
    def __init__(self, name: str, client: GraphClient, shown_calendars: Callable[[], list[str]],
                 default_calendar: Callable[[], str], timezone: Callable[[], str]):
        self._name = name
        self._client = client
        self._shown_calendars = shown_calendars
        self._default_calendar = default_calendar
        self._timezone = timezone
        self._calendar_ids: Optional[dict[str, str]] = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def service(self) -> str:
        return "Microsoft 365"

    @property
    def shown_calendars(self) -> list[str]:
        return self._shown_calendars()

    @property
    def default_calendar(self) -> str:
        return self._default_calendar()

    def _ids(self, refresh: bool = False) -> dict[str, str]:
        """Calendar name -> id, looked up again on `refresh`."""
        if self._calendar_ids is None or refresh:
            items = self._client.get_all("/me/calendars", {"$top": 100, "$select": "id,name"})
            self._calendar_ids = {str(c.get("name") or ""): c["id"] for c in items}
            trace(f"{self.name}: {len(self._calendar_ids)} calendars")
        return self._calendar_ids

    def calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(name) for name in self._ids(refresh=True)]

    def _home_tz(self):
        return pytz.timezone(self._timezone())

    def _as_datetime(self, value: dict, all_day: bool) -> datetime:
        """A Graph `dateTimeTimeZone` as an aware datetime: an all-day bound is the midnight UTC
        of its date (the renderer's convention), a timed one is in the zone Graph names —
        the home zone when that name is not an IANA one."""
        text = str(value.get("dateTime") or "")[:19]
        naive = datetime.fromisoformat(text)
        if all_day:
            return datetime.combine(naive.date(), datetime.min.time()).replace(tzinfo=pytz.UTC)
        zone_name = str(value.get("timeZone") or "")
        try:
            zone = pytz.timezone(zone_name) if zone_name else self._home_tz()
        except pytz.UnknownTimeZoneError:
            trace(f"{self.name}: Graph named the zone '{zone_name}', taking the home zone")
            zone = self._home_tz()
        return zone.localize(naive)

    def events(self, calendar_names: list[str], start: date, end: date) -> list[Event]:
        wanted = {name.lower() for name in calendar_names}
        headers = {"Prefer": f'outlook.timezone="{self._timezone()}"'}
        params = {"startDateTime": f"{start.isoformat()}T00:00:00", "endDateTime": f"{end.isoformat()}T00:00:00",
                  "$top": PAGE_SIZE, "$select": EVENT_SELECT}
        found = []
        for name, calendar_id in self._ids().items():
            if name.lower() not in wanted:
                continue
            for item in self._client.get_all(f"/me/calendars/{calendar_id}/calendarView", params, headers):
                all_day = bool(item.get("isAllDay"))
                found.append(Event(self._as_datetime(item.get("start") or {}, all_day),
                                   self._as_datetime(item.get("end") or {}, all_day),
                                   str(item.get("subject") or ""), calendar=name, account=self.name, all_day=all_day,
                                   id=str(item.get("id") or ""), recurring=str(item.get("type") or "") in RECURRING_TYPES))
        trace(f"{self.name}: {len(found)} event(s) in {sorted(wanted)} between {start} and {end}")
        return found

    def _event_path(self, calendar_name: str, event: Event) -> str:
        """The path of an event read earlier; an id names the event whatever calendar holds it,
        the calendar is checked so a stale name is refused the way create_event refuses it."""
        if calendar_name not in self._ids():
            raise ValueError(f"Calendar '{calendar_name}' not found. Available: {list(self._ids())}")
        if not event.id:
            raise ValueError(f"'{event.title}' has no event id")
        return f"/me/events/{event.id}"

    def _bounds(self, start: datetime, end: datetime, all_day: bool) -> dict:
        """The `start`, `end` and `isAllDay` of an event payload in the home timezone; an
        all-day event runs from midnight to the midnight after its last day."""
        zone_name = self._timezone()
        zone = pytz.timezone(zone_name)
        if all_day:
            first = datetime.combine(start.date(), datetime.min.time())
            last = datetime.combine(max(end.date(), start.date() + timedelta(days=1)), datetime.min.time())
        else:
            first, last = start.astimezone(zone).replace(tzinfo=None), end.astimezone(zone).replace(tzinfo=None)
        return {"isAllDay": all_day,
                "start": {"dateTime": first.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone_name},
                "end": {"dateTime": last.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone_name}}

    def delete_event(self, calendar_name: str, event: Event) -> None:
        """One occurrence of a series is deleted alone: Graph reads an occurrence id as that one."""
        path = self._event_path(calendar_name, event)
        try:
            self._client.delete(path)
        except GraphError as e:
            if e.status == 404:
                raise ValueError(f"'{event.title}' is no longer in '{calendar_name}'") from e
            raise
        info(f"{self.name}: event '{event.title}' ({event.id}) deleted from '{calendar_name}'")

    def move_event(self, calendar_name: str, event: Event, start: datetime, end: datetime, all_day: bool) -> None:
        """A PATCH of the bounds; an occurrence of a series becomes an exception of it."""
        path = self._event_path(calendar_name, event)
        try:
            self._client.patch(path, self._bounds(start, end, all_day))
        except GraphError as e:
            if e.status == 404:
                raise ValueError(f"'{event.title}' is no longer in '{calendar_name}'") from e
            raise
        info(f"{self.name}: event '{event.title}' ({event.id}) in '{calendar_name}' moved to {start.isoformat()} - {end.isoformat()}"
             f"{' (all day)' if all_day else ''}")

    def create_event(self, calendar_name: str, event: Event) -> None:
        ids = self._ids()
        calendar_id = ids.get(calendar_name)
        if calendar_id is None:
            raise ValueError(f"Calendar '{calendar_name}' not found. Available: {list(ids)}")
        payload = {"subject": event.title, **self._bounds(event.start, event.end, event.all_day)}
        self._client.post(f"/me/calendars/{calendar_id}/events", payload)
        info(f"{self.name}: event '{event.title}' created in '{calendar_name}'")

    def check(self) -> bool:
        self._ids(refresh=True)
        return True
