"""Refreshes the coming-week calendar section from every calendar account every 15 minutes;
runs only while a calendar source integration is on (PromptManager starts it when the
IntegrationMonitor has one), so it never shows "not configured" for an install without one."""
from assistant.integration.calendar.CalendarAccess import CalendarAccess
from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace


class CalendarSectionGenerator(PromptSectionGenerator):
    key = "calendar"
    needs = CalendarSource
    heading = "# CALENDAR EVENTS COMING WEEK"
    intro = ("The coming week of the calendars shown by default: one line per calendar account naming the calendars "
             "it shows and the account's other calendars, which it leaves out, then the events of every account in "
             "one list; an event names its calendar in brackets when more than one is shown. The `calendar` toolkit "
             "fetches a calendar that is not shown, and creates, moves or deletes an event.")

    UPDATE_INTERVAL = 15 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "CalendarData")

    def _fetch_content(self) -> str:
        trace("Fetching the coming week from the calendar accounts...")
        return CalendarAccess.current().coming_week()
