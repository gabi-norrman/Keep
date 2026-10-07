from typing import List

from assistant.integration.Integration import Integration
from assistant.integration.calendar.CalendarAccount import CalendarAccount, split_names
from assistant.integration.calendar.CalendarSource import CalendarSource
from common.ConfigManager import config
from common.logging import trace
from integrations.caldav.CalDavCalendarAccount import CalDavCalendarAccount

ICLOUD_CALDAV_URL = 'https://caldav.icloud.com/'


class CalDAVIntegration(Integration, CalendarSource):
    """The iCloud calendars.

    Reads the household's calendars and creates events in them, so Keep can say what is on the
    schedule and add to it when asked.

    A CalendarSource holding one CalDavCalendarAccount on the iCloud CalDAV server, read from the
    `calendar` section."""
    sensitive = True

    def __init__(self):
        Integration.__init__(self)
        self._account = CalDavCalendarAccount(
            "iCloud", ICLOUD_CALDAV_URL,
            credentials=lambda: (config.get('calendar.username', ""), config.get('calendar.password', "")),
            shown_calendars=self.shown_calendar_names,
            default_calendar=lambda: config.get('calendar.default_calendar', 'Home'))

    @property
    def name(self) -> str:
        return "CalDAV"

    @property
    def config_keys(self) -> List[str]:
        return ["calendar"]

    def calendar_accounts(self) -> list[CalendarAccount]:
        return [self._account]

    @staticmethod
    def shown_calendar_names() -> list[str]:
        """`calendar.shown_calendars`: the calendars the prompt carries. Empty means all of them."""
        # An empty default, not None: ConfigManager.get raises KeyError for an absent key without one.
        return split_names(config.get('calendar.shown_calendars', []))

    def check_health(self) -> bool:
        if not config.get('calendar.username') or not config.get('calendar.password'):
            trace("CalDAV: no username or password, health check fails")
            return False
        return self._account.check()
