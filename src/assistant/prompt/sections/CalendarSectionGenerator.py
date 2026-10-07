"""Refreshes the coming-week calendar section from every calendar account every 15 minutes."""
from assistant.integration.calendar.CalendarAccess import CalendarAccess
from assistant.prompt.PromptSectionGenerator import PromptSectionGenerator
from common.logging import trace


class CalendarSectionGenerator(PromptSectionGenerator):
    key = "calendar"

    UPDATE_INTERVAL = 15 * 60
    # The prompt sections start before the launcher registers the integrations, so the first
    # fetch may find no calendar integration at all; until one is registered the section looks
    # again this often instead of showing "not configured" for a whole UPDATE_INTERVAL.
    STARTING_POLL_INTERVAL = 2

    def __init__(self, prompt_manager):
        super().__init__(prompt_manager, "CalendarData")
        self._starting = True

    def _start_monitoring(self):
        self._update_content()
        while not self._stop_event.is_set():
            self._wait(self.STARTING_POLL_INTERVAL if self._starting else self.UPDATE_INTERVAL)
            if not self._stop_event.is_set():
                self._update_content()

    def _fetch_content(self) -> str:
        trace("Fetching the coming week from the calendar accounts...")
        access = CalendarAccess.current()
        self._starting = access.starting
        return access.coming_week()
