"""The one calendar the prompt section, the commands and the memory curator read: every calendar
account of every enabled and configured integration, presented as one.

`fetch` is the CALENDAR EVENTS COMING WEEK text — one header line per account naming the
calendars it shows and the ones left out, then one chronological list of the shown calendars'
events across accounts, each line tagged with its calendar when more than one is shown; an
account that fails is named as such while the others still show, and the whole week is
EVENTS_UNAVAILABLE only when none could be read (never NO_EVENTS, which would read as an empty
week). `load` is the `load_calendar` command, `create` the `create_event` one; both name a
calendar across accounts, `Account/Name` when two accounts have one by that name."""
from datetime import date, datetime, timedelta
from typing import Optional

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
NO_ACCOUNT_HINT = ("No calendar account is configured. Add one under caldav.accounts (name, preset icloud or a url, "
                   "username, password) — see Setting things up: list_integrations, config_help(\"caldav\"), config_request.")


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
        """One header line per account — the calendars shown and the ones left out — then the
        events of the shown ones in one list."""
        if not self._accounts:
            info(f"Calendar: no account configured, the prompt gets '{NOT_CONFIGURED}'")
            return NOT_CONFIGURED
        headers, events, failed, shown_total = [], [], 0, 0
        for account in self._accounts:
            try:
                header, shown = self._header(account)
                events.extend(account.events([c.name for c in shown], start_date, end_date))
                headers.append(header)
                shown_total += sum(1 for c in shown if c.holds_events)
            except Exception as e:
                error(f"Error fetching calendar events of {account.name}: {type(e).__name__}: {e}")
                failed += 1
                headers.append(f"({account.name}: calendar could not be loaded)")
        if failed == len(self._accounts):
            error(f"No calendar account could be read, the prompt gets '{EVENTS_UNAVAILABLE}'")
            return EVENTS_UNAVAILABLE
        trace(f"Calendar: {len(events)} event(s) from {len(self._accounts) - failed} account(s), {shown_total} calendar(s) shown")
        return "\n".join(headers) + "\n\n" + render(events, start_date, end_date, tag_calendars=shown_total > 1)

    @staticmethod
    def _label(account: CalendarAccount) -> str:
        return f"{account.name} ({account.service})" if account.service else account.name

    @classmethod
    def _header(cls, account: CalendarAccount) -> tuple[str, list[CalendarInfo]]:
        """`Family (CalDAV): Home, Kids — not shown: Birthdays`, and the calendars whose events are shown."""
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
        header = f"{cls._label(account)}: " + (", ".join(shown_labels) or "none shown")
        if hidden_labels:
            header += " — not shown: " + ", ".join(hidden_labels)
        return header, shown

    def _resolve(self, name: str) -> tuple[Optional[tuple[CalendarAccount, CalendarInfo]], str]:
        """The account and calendar `name` means — `Kids`, or `Family/Kids` when two accounts
        have a Kids — or None and the reason; the reason names what exists."""
        wanted = name.strip().strip("'\"")
        account_part, _, calendar_part = wanted.rpartition("/")
        found: list[tuple[CalendarAccount, CalendarInfo]] = []
        available: list[str] = []
        for account in self._accounts:
            if account_part and account.name.lower() != account_part.strip().lower():
                continue
            for calendar in account.calendars():
                if calendar.holds_events:
                    available.append(f"{account.name}/{calendar.name}" if len(self._accounts) > 1 else calendar.name)
                if calendar.name.lower() == (calendar_part if account_part else wanted).strip().lower():
                    found.append((account, calendar))
        if not found:
            info(f"Calendar: no calendar named '{wanted}'")
            return None, f"no calendar named '{wanted}'. Available: {', '.join(available) or 'none'}"
        if len(found) > 1:
            choices = ", ".join(f"{a.name}/{c.name}" for a, c in found)
            info(f"Calendar: '{wanted}' is ambiguous: {choices}")
            return None, f"'{wanted}' is in several accounts — name one: {choices}"
        return found[0], ""

    def load(self, names: list[str]) -> str:
        """The coming week of the named calendars, each under its own header; the `load_calendar` command."""
        if not self._accounts:
            raise ValueError(NO_ACCOUNT_HINT)
        start_date = date.today()
        end_date = start_date + timedelta(days=CALENDAR_DAYS_AHEAD)
        parts = []
        for name in names:
            found, reason = self._resolve(name)
            if found is None:
                parts.append(f"load_calendar failed: {reason}")
                continue
            account, calendar = found
            info(f"Loading calendar '{calendar.name}' of {account.name} {start_date} to {end_date}")
            label = f"{account.name}/{calendar.name}" if len(self._accounts) > 1 else calendar.name
            parts.append(f"Calendar '{label}', {start_date} to {end_date}:\n"
                         + render(account.events([calendar.name], start_date, end_date), start_date, end_date))
        return "\n\n".join(parts)

    def create(self, title: str, date_str: str, start_time: str, end_time: str, calendar_name: str = "") -> str:
        """An event in the named calendar, else the first account's default calendar (matched
        to a listed name whatever its case, or handed over as an alias the account knows);
        returns the `Account/Calendar` it went to and raises ValueError when there is no
        account or no such calendar."""
        if not self._accounts:
            raise ValueError(NO_ACCOUNT_HINT)
        local_tz = pytz.timezone(config.get_home_timezone())
        dt_start = local_tz.localize(datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M"))
        dt_end = local_tz.localize(datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M"))
        if calendar_name.strip():
            found, reason = self._resolve(calendar_name)
            if found is None:
                raise ValueError(reason)
            account, calendar = found
            target_name = calendar.name
        else:
            account = self._accounts[0]
            target_name = account.default_calendar
            calendars = account.calendars()
            if not target_name:
                target_name = next((c.name for c in calendars if c.holds_events), "")
                trace(f"{account.name}: no default calendar configured, using '{target_name}'")
            listed = next((c.name for c in calendars if c.name.lower() == target_name.lower()), None)
            if listed is None:
                # Not a listed name: an alias the account resolves itself (Google's "primary"),
                # or unknown — the account's create_event raises for that, naming what exists.
                trace(f"{account.name}: default calendar '{target_name}' is not a listed name, the account resolves it")
            target_name = listed or target_name
        account.create_event(target_name, Event(dt_start, dt_end, title, calendar=target_name, account=account.name))
        info(f"Created calendar event: {title} on {date_str} {start_time}-{end_time} in {account.name}/{target_name}")
        return f"{account.name}/{target_name}"
