from typing import List

from assistant.integration.Accounts import AccountHealth, named_entries
from assistant.integration.Integration import Integration
from assistant.integration.calendar.CalendarAccount import CalendarAccount, split_names
from assistant.integration.calendar.CalendarSource import CalendarSource
from common.ConfigManager import ConfigManager
from common.logging import info, trace
from integrations.caldav.CalDavCalendarAccount import CalDavCalendarAccount

SECTION = "caldav"
ACCOUNTS_KEY = f"{SECTION}.accounts"
PRESET_URLS = {"icloud": "https://caldav.icloud.com/"}
# Where an event that names no calendar goes when the entry does not say.
DEFAULT_CALENDAR = "Home"


class CalDAVIntegration(AccountHealth, Integration, CalendarSource):
    """Calendars over CalDAV, iCloud or any server: read and add events.

    Reads the calendars of every account listed under caldav.accounts and creates events in
    them, so Keep can say what is on the schedule and add to it when asked.

    A CalendarSource building one CalDavCalendarAccount per `caldav.accounts[]` entry: `name`
    (unique), `preset: icloud` or `url`, `username`, `password`, `default_calendar`,
    `shown_calendars`. The credentials and calendar names are read from config on every call;
    a changed list of accounts needs restart_integration. The health check tries every account
    and names the failing ones."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        self._accounts = self._build_accounts()

    @property
    def name(self) -> str:
        return "CalDAV"

    @property
    def config_keys(self) -> List[str]:
        return [ACCOUNTS_KEY]

    def calendar_accounts(self) -> list[CalendarAccount]:
        return list(self._accounts)

    def check_health(self) -> bool:
        if not self._accounts:
            trace("CalDAV: no account to check")
            return False
        return self.check_accounts(self._accounts)

    def _health_accounts(self) -> list:
        return self._accounts

    @staticmethod
    def _entry(index: int) -> dict:
        """The `caldav.accounts[index]` section as config.yaml holds it now, {} when gone."""
        entries = ConfigManager.get_instance().get(ACCOUNTS_KEY, [])
        if isinstance(entries, list) and index < len(entries) and isinstance(entries[index], dict):
            return entries[index]
        return {}

    @classmethod
    def _build_accounts(cls) -> list[CalDavCalendarAccount]:
        config = ConfigManager.get_instance()
        accounts = []
        for index, entry in named_entries(config.get(ACCOUNTS_KEY, []), SECTION):
            name = str(entry["name"]).strip()
            preset = str(entry.get("preset") or "").strip().lower()
            url = str(entry.get("url") or "").strip() or PRESET_URLS.get(preset, "")
            if preset and preset not in PRESET_URLS:
                info(f"CalDAV: account '{name}' names an unknown preset '{preset}' (known: {', '.join(PRESET_URLS)}), skipped")
                continue
            if not url:
                info(f"CalDAV: account '{name}' has neither a preset nor a url, skipped")
                continue
            accounts.append(CalDavCalendarAccount(
                name, url,
                credentials=lambda i=index: (str(cls._entry(i).get("username") or ""), str(cls._entry(i).get("password") or "")),
                shown_calendars=lambda i=index: split_names(cls._entry(i).get("shown_calendars", [])),
                default_calendar=lambda i=index: str(cls._entry(i).get("default_calendar") or DEFAULT_CALENDAR)))
            trace(f"CalDAV: account '{name}' on {url}")
        info(f"CalDAV: {len(accounts)} account(s): {', '.join(a.name for a in accounts) or 'none'}")
        return accounts
