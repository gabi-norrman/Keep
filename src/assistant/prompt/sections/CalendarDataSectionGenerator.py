from datetime import date, timedelta

from ..PromptSectionGenerator import PromptSectionGenerator
from common.logging import info, trace
from ...integration.integrations.CalDAVIntegration import CalDAVIntegration, CALENDAR_DAYS_AHEAD


class CalendarDataSectionGenerator(PromptSectionGenerator):
    """Refreshes the upcoming-week calendar section from iCloud CalDAV every 15 minutes."""

    UPDATE_INTERVAL = 15 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "CalendarData")

    def _fetch_content(self) -> str:
        """Fetch calendar data from iCloud."""
        trace("Fetching calendar from iCloud over CalDAV...")

        start_date = date.today()
        end_date = start_date + timedelta(days=CALENDAR_DAYS_AHEAD)

        calendar_summary = CalDAVIntegration.fetch_calendar_events(start_date, end_date)

        return calendar_summary
