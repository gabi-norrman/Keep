"""The one calendar the prompt section, the commands and the memory curator read: every calendar
account of every enabled and configured integration, presented as one.

`fetch` is the CALENDAR EVENTS COMING WEEK text — the names of the calendars shown and left out,
then one chronological list of the shown ones' events across accounts; an account that fails is
named as such while the others still show, and the whole week is EVENTS_UNAVAILABLE only when
none could be read (never NO_EVENTS, which would read as an empty week). `load` is the
`load_calendar` command, `create` the `create_event` one."""
from datetime import date, datetime, timedelta

import pytz

from common.ConfigManager import config
from common.logging import info, error, trace
from .CalendarAccount import CalendarAccount, CalendarInfo
from .CalendarSource import CalendarSource
from .Event import Event, render

# How far ahead the prompt's calendar section and `load_calendar` look.
CALENDAR_DAYS_AHEAD = 8
# What the prompt carries when the fetch failed.
EVENTS_UNAVAILABLE = "(Calendar events could not be loaded)"
NOT_CONFIGURED = "(Calendar not configured)"
NO_ACCOUNT_HINT = ("No calendar account is configured (calendar.username / calendar.password for iCloud). "
                   "Use list_integrations and config_help to set it up.")


class CalendarAccess:
    def __init__(self, accounts: list[CalendarAccount]):
        self._accounts = list(accounts)

    @classmethod
    def current(cls) -> "CalendarAccess":
        """The accounts of every registered CalendarSource whose config keys are set — resolved
        per call, so a restarted or newly configured integration is what the next read uses."""
        from assistant.integration.IntegrationMonitor import IntegrationMonitor
        accounts: list[CalendarAccount] = []
        for integration in IntegrationMonitor.get_instance().of_type(CalendarSource):
            missing = integration.missing_config_keys()
            if missing:
                trace(f"Calendar: {integration.name} is enabled but not configured ({', '.join(missing)}), no accounts from it")
                continue
            accounts.extend(integration.calendar_accounts())
        if not accounts:
            trace("Calendar: no calendar account configured")
        return cls(accounts)

    @property
    def accounts(self) -> list[CalendarAccount]:
        return list(self._accounts)

    def coming_week(self) -> str:
        start = date.today()
        return self.fetch(start, start + timedelta(days=CALENDAR_DAYS_AHEAD))

    def fetch(self, start_date: date, end_date: date) -> str:
        """The names of the calendars — shown and left out — followed by the events of the shown ones."""
        if not self._accounts:
            info(f"Calendar: no account configured, the prompt gets '{NOT_CONFIGURED}'")
            return NOT_CONFIGURED
        headers, events, failed = [], [], 0
        for account in self._accounts:
            try:
                header, shown = self._header(account)
                events.extend(account.events([c.name for c in shown], start_date, end_date))
                headers.append(header if len(self._accounts) == 1 else f"{account.name}: {header}")
            except Exception as e:
                error(f"Error fetching calendar events of {account.name}: {type(e).__name__}: {e}")
                failed += 1
                headers.append(f"({account.name}: calendar could not be loaded)")
        if failed == len(self._accounts):
            error(f"No calendar account could be read, the prompt gets '{EVENTS_UNAVAILABLE}'")
            return EVENTS_UNAVAILABLE
        return "\n".join(headers) + "\n\n" + render(events, start_date, end_date)

    @staticmethod
    def _header(account: CalendarAccount) -> tuple[str, list[CalendarInfo]]:
        """The account's header lines and the calendars whose events are shown."""
        calendars = account.calendars()
        shown_names = {name.lower() for name in account.shown_calendars}
        if shown_names:
            shown = [c for c in calendars if c.name.lower() in shown_names]
            missing = shown_names - {c.name.lower() for c in shown}
            if missing:
                info(f"{account.name}: shown_calendars names calendars the account does not have: {sorted(missing)}. "
                     f"Available: {[c.name for c in calendars]}")
        else:
            trace(f"{account.name}: shown_calendars not set, showing all {len(calendars)} calendars")
            shown = calendars
        # A Reminders list among the shown ones is searched (it holds no events) but not named.
        named = [c for c in calendars if c.holds_events]
        shown_labels = [c.name for c in named if c in shown]
        hidden_labels = [c.name for c in named if c not in shown]
        trace(f"{account.name}: calendars shown: {shown_labels}, not shown: {hidden_labels}")
        header = "Calendars shown below: " + (", ".join(shown_labels) or "none")
        if hidden_labels:
            header += "\nOther calendars, not shown (`load_calendar(name)` loads one): " + ", ".join(hidden_labels)
        return header, shown

    def load(self, names: list[str]) -> str:
        """The coming week of the named calendars, each under its own header; the `load_calendar` command."""
        if not self._accounts:
            raise ValueError(NO_ACCOUNT_HINT)
        start_date = date.today()
        end_date = start_date + timedelta(days=CALENDAR_DAYS_AHEAD)
        by_name: dict[str, tuple[CalendarAccount, CalendarInfo]] = {}
        available: list[str] = []
        for account in self._accounts:
            for calendar in account.calendars():
                by_name.setdefault(calendar.name.lower(), (account, calendar))
                if calendar.holds_events:
                    available.append(calendar.name)
        parts = []
        for name in names:
            found = by_name.get(name.strip().strip("'\"").lower())
            if found is None:
                info(f"load_calendar: no calendar named '{name}'")
                parts.append(f"load_calendar failed: no calendar named '{name}'. Available: {', '.join(available) or 'none'}")
                continue
            account, calendar = found
            info(f"Loading calendar '{calendar.name}' {start_date} to {end_date}")
            parts.append(f"Calendar '{calendar.name}', {start_date} to {end_date}:\n"
                         + render(account.events([calendar.name], start_date, end_date), start_date, end_date))
        return "\n\n".join(parts)

    def create(self, title: str, date_str: str, start_time: str, end_time: str) -> None:
        """An event in the first account's default calendar; raises ValueError when there is no
        account or no such calendar."""
        if not self._accounts:
            raise ValueError(NO_ACCOUNT_HINT)
        local_tz = pytz.timezone(config.get_home_timezone())
        dt_start = local_tz.localize(datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M"))
        dt_end = local_tz.localize(datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M"))
        account = self._accounts[0]
        target_name = account.default_calendar
        calendars = account.calendars()
        if target_name not in [c.name for c in calendars]:
            raise ValueError(f"Calendar '{target_name}' not found. Available: {[c.name for c in calendars]}")
        account.create_event(target_name, Event(dt_start, dt_end, title, calendar=target_name, account=account.name))
        info(f"Created calendar event: {title} on {date_str} {start_time}-{end_time}")
