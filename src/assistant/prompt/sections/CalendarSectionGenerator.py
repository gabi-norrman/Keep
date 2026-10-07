"""Refreshes the coming-week calendar section from every calendar account every 15 minutes."""
from assistant.integration.calendar.CalendarAccess import CalendarAccess
from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace


class CalendarSectionGenerator(PromptSectionGenerator):
    key = "calendar"

    UPDATE_INTERVAL = 15 * 60

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "CalendarData")

    def _fetch_content(self) -> str:
        trace("Fetching the coming week from the calendar accounts...")
        return CalendarAccess.current().coming_week()
