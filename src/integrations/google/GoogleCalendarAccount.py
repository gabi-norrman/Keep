"""A CalendarAccount over the Google Calendar API v3: the calendars of a Google account, their events over a window, and a new event.

`calendarList` names the calendars (the primary one answers to "primary" and to the account's
address as well as to its own name); `events` with `singleEvents` expands recurrences on the
server, answers in the home timezone and is paged through `nextPageToken`. A timed bound
(`dateTime`, with its offset) becomes an aware datetime, an all-day one (`date`) the
midnight-UTC datetimes the renderer expects. A new event is posted with the home timezone.

The calls go over `requests` with the account's bearer token: a 401 means the token is gone
(the sign-in hint), a 403 rate limit or a 429 is retried once after a second, anything else
that fails raises GoogleCalendarError with Google's short message."""
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from urllib.parse import quote

import pytz
import requests

from assistant.integration.calendar.CalendarAccount import CalendarAccount, CalendarInfo
from assistant.integration.calendar.Event import Event
from common.logging import error, info, trace
from integrations.google.GoogleAuth import GoogleAuth, NotSignedIn, sign_in_again_hint

BASE_URL = "https://www.googleapis.com/calendar/v3"
TIMEOUT = 30
PAGE_SIZE = 250
RETRY_AFTER = 1
RATE_LIMIT_REASONS = ("rateLimitExceeded", "userRateLimitExceeded")
PRIMARY_ALIASES = ("primary",)


class GoogleCalendarError(Exception):
    """The Calendar API answered with an error status; `status` carries it."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GoogleCalendarAccount(CalendarAccount):
    def __init__(self, name: str, auth: GoogleAuth, shown_calendars: Callable[[], list[str]],
                 default_calendar: Callable[[], str], timezone: Callable[[], str], address: Callable[[], str]):
        self._name = name
        self._auth = auth
        self._shown_calendars = shown_calendars
        self._default_calendar = default_calendar
        self._timezone = timezone
        self._address = address
        self._calendar_ids: Optional[dict[str, str]] = None
        self._primary: Optional[tuple[str, str]] = None      # (name, id) of the primary calendar

    @property
    def name(self) -> str:
        return self._name

    @property
    def service(self) -> str:
        return "Google"

    @property
    def shown_calendars(self) -> list[str]:
        return self._shown_calendars()

    @property
    def default_calendar(self) -> str:
        return self._default_calendar()

    # --- the API -----------------------------------------------------------------------------

    def _call(self, method: str, path: str, params: Optional[dict] = None, json: Optional[dict] = None,
              retried: bool = False) -> dict:
        token = self._auth.token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        started = time.time()
        response = requests.request(method, BASE_URL + path, params=params, headers=headers, json=json, timeout=TIMEOUT)
        trace(f"{self.name}: {method} {path} -> {response.status_code} in {time.time() - started:.1f}s")
        if response.status_code == 401:
            info(f"{self.name}: Google answered 401 — the token is not accepted")
            raise NotSignedIn(f"token not accepted, {sign_in_again_hint(self.name)}")
        reason = self._reason(response) if response.status_code in (403, 429) else ""
        if response.status_code == 429 or reason in RATE_LIMIT_REASONS:
            if retried:
                raise GoogleCalendarError(response.status_code, "Google is rate-limiting the account, try again in a minute")
            info(f"{self.name}: Google answered {response.status_code} ({reason or 'too many requests'}), retrying once in {RETRY_AFTER}s")
            time.sleep(RETRY_AFTER)
            return self._call(method, path, params, json, retried=True)
        if response.status_code >= 400:
            message = self._message(response)
            error(f"{self.name}: Google {method} {path} failed: {response.status_code} {message}")
            raise GoogleCalendarError(response.status_code, message)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def _get_all(self, path: str, params: dict) -> list:
        """Every item of a paged collection, following `nextPageToken` to the end."""
        items: list = []
        page = self._call("GET", path, params)
        items.extend(page.get("items", []))
        pages = 1
        while page.get("nextPageToken"):
            page = self._call("GET", path, {**params, "pageToken": page["nextPageToken"]})
            items.extend(page.get("items", []))
            pages += 1
        if pages > 1:
            trace(f"{self.name}: {len(items)} items over {pages} pages of {path}")
        return items

    @staticmethod
    def _reason(response) -> str:
        try:
            errors = (response.json().get("error") or {}).get("errors") or []
            return str(errors[0].get("reason") or "") if errors else ""
        except (ValueError, AttributeError, IndexError):
            return ""

    @staticmethod
    def _message(response) -> str:
        try:
            err = response.json().get("error") or {}
            return str(err.get("message") or err.get("status") or response.text)[:300]
        except (ValueError, AttributeError):
            return (response.text or f"HTTP {response.status_code}")[:300]

    # --- calendars ---------------------------------------------------------------------------

    def _ids(self, refresh: bool = False) -> dict[str, str]:
        """Calendar name -> id, looked up again on `refresh`; remembers which one is primary."""
        if self._calendar_ids is None or refresh:
            items = self._get_all("/users/me/calendarList", {"maxResults": PAGE_SIZE})
            ids: dict[str, str] = {}
            self._primary = None
            for item in items:
                name = str(item.get("summaryOverride") or item.get("summary") or item.get("id") or "")
                ids[name] = item["id"]
                if item.get("primary"):
                    self._primary = (name, item["id"])
            self._calendar_ids = ids
            trace(f"{self.name}: {len(ids)} calendars" + (f", primary '{self._primary[0]}'" if self._primary else ""))
        return self._calendar_ids

    def _resolve(self, wanted: str) -> Optional[tuple[str, str]]:
        """(name, id) of the calendar `wanted` names: its own name, or "primary" / the account
        address for the primary one."""
        ids = self._ids()
        lowered = wanted.strip().lower()
        for name, calendar_id in ids.items():
            if name.lower() == lowered:
                return name, calendar_id
        if self._primary and lowered in PRIMARY_ALIASES + ((self._address() or "").lower(),):
            return self._primary
        return None

    def calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(name) for name in self._ids(refresh=True)]

    def listed_name(self, alias: str) -> Optional[str]:
        resolved = self._resolve(alias)
        return resolved[0] if resolved else None

    # --- events ------------------------------------------------------------------------------

    def _home_tz(self):
        return pytz.timezone(self._timezone())

    def _as_datetime(self, value: dict) -> tuple[datetime, bool]:
        """A Google start/end as (aware datetime, all_day): a `date` is the midnight UTC of its
        day (the renderer's convention), a `dateTime` keeps the offset Google sent."""
        if value.get("date"):
            return datetime.combine(date.fromisoformat(str(value["date"])), datetime.min.time()).replace(tzinfo=pytz.UTC), True
        parsed = datetime.fromisoformat(str(value.get("dateTime") or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            zone_name = str(value.get("timeZone") or "")
            try:
                zone = pytz.timezone(zone_name) if zone_name else self._home_tz()
            except pytz.UnknownTimeZoneError:
                zone = self._home_tz()
            parsed = zone.localize(parsed)
        return parsed, False

    def events(self, calendar_names: list[str], start: date, end: date) -> list[Event]:
        zone = self._home_tz()
        params = {"timeMin": zone.localize(datetime.combine(start, datetime.min.time())).isoformat(),
                  "timeMax": zone.localize(datetime.combine(end, datetime.min.time())).isoformat(),
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": PAGE_SIZE, "timeZone": self._timezone()}
        found = []
        seen_ids: set[str] = set()
        for wanted in calendar_names:
            resolved = self._resolve(wanted)
            if resolved is None:
                info(f"{self.name}: no calendar named '{wanted}', skipped")
                continue
            name, calendar_id = resolved
            if calendar_id in seen_ids:
                continue
            seen_ids.add(calendar_id)
            for item in self._get_all(f"/calendars/{quote(calendar_id, safe='')}/events", params):
                if item.get("status") == "cancelled":
                    continue
                start_dt, all_day = self._as_datetime(item.get("start") or {})
                end_dt, _ = self._as_datetime(item.get("end") or item.get("start") or {})
                found.append(Event(start_dt, end_dt, str(item.get("summary") or "(no title)"),
                                   calendar=name, account=self.name, all_day=all_day, id=str(item.get("id") or ""),
                                   recurring=bool(item.get("recurringEventId"))))
        trace(f"{self.name}: {len(found)} event(s) in {calendar_names} between {start} and {end}")
        return found

    def _event_path(self, calendar_name: str, event: Event) -> tuple[str, str]:
        """The path of an event read earlier, and the listed name of its calendar."""
        resolved = self._resolve(calendar_name)
        if resolved is None:
            raise ValueError(f"Calendar '{calendar_name}' not found. Available: {list(self._ids())}")
        if not event.id:
            raise ValueError(f"'{event.title}' has no event id")
        name, calendar_id = resolved
        return f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event.id, safe='')}", name

    def _bounds(self, start: datetime, end: datetime, all_day: bool) -> dict:
        """The `start` and `end` of an event payload: dates for an all-day event, else the
        times in the home timezone."""
        zone_name = self._timezone()
        zone = pytz.timezone(zone_name)
        if all_day:
            last = max(end.date(), start.date() + timedelta(days=1))
            return {"start": {"date": start.date().isoformat()}, "end": {"date": last.isoformat()}}
        return {"start": {"dateTime": start.astimezone(zone).replace(tzinfo=None).isoformat(), "timeZone": zone_name},
                "end": {"dateTime": end.astimezone(zone).replace(tzinfo=None).isoformat(), "timeZone": zone_name}}

    def delete_event(self, calendar_name: str, event: Event) -> None:
        """One instance of a series is deleted alone: Google reads an instance id as that occurrence."""
        path, name = self._event_path(calendar_name, event)
        try:
            self._call("DELETE", path)
        except GoogleCalendarError as e:
            if e.status in (404, 410):
                raise ValueError(f"'{event.title}' is no longer in '{name}'") from e
            raise
        info(f"{self.name}: event '{event.title}' ({event.id}) deleted from '{name}'")

    def move_event(self, calendar_name: str, event: Event, start: datetime, end: datetime, all_day: bool) -> None:
        """A PATCH of the bounds; an instance of a series becomes an exception of it."""
        path, name = self._event_path(calendar_name, event)
        try:
            self._call("PATCH", path, json=self._bounds(start, end, all_day))
        except GoogleCalendarError as e:
            if e.status in (404, 410):
                raise ValueError(f"'{event.title}' is no longer in '{name}'") from e
            raise
        info(f"{self.name}: event '{event.title}' ({event.id}) in '{name}' moved to {start.isoformat()} - {end.isoformat()}"
             f"{' (all day)' if all_day else ''}")

    def create_event(self, calendar_name: str, event: Event) -> None:
        resolved = self._resolve(calendar_name)
        if resolved is None:
            raise ValueError(f"Calendar '{calendar_name}' not found. Available: {list(self._ids())}")
        name, calendar_id = resolved
        payload = {"summary": event.title, **self._bounds(event.start, event.end, event.all_day)}
        self._call("POST", f"/calendars/{quote(calendar_id, safe='')}/events", json=payload)
        info(f"{self.name}: event '{event.title}' created in '{name}'")

    def check(self) -> bool:
        self._call("GET", "/users/me/calendarList", {"maxResults": 1})
        return True
