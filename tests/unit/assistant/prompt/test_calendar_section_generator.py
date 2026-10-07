"""The calendar section: the coming week of every calendar account, refreshed every 15 minutes."""
from unittest.mock import patch

import pytest

from assistant.prompt.sections.CalendarSectionGenerator import CalendarSectionGenerator


@pytest.mark.unit
class TestCalendarSectionGenerator:
    def test_the_content_is_the_composites_coming_week(self):
        with patch('assistant.prompt.sections.CalendarSectionGenerator.CalendarAccess.current') as current:
            current.return_value.coming_week.return_value = "Calendars shown below: Home\n\n(No upcoming events)"
            section = CalendarSectionGenerator(None)
            assert section._fetch_content() == "Calendars shown below: Home\n\n(No upcoming events)"

    def test_the_key_is_a_contract_with_the_placeholder_of_the_prompt_template(self):
        from common.Paths import RES_DIR
        template = (RES_DIR / "prompt.md").read_text(encoding="utf-8")
        assert "{" + CalendarSectionGenerator.key.upper() + "}" in template

    def test_polls_again_soon_while_the_integrations_are_still_starting(self):
        """2026-10-07: production's first fetch ran before CalDAV was registered and the prompt
        said "not configured" for the whole 15-minute interval."""
        from unittest.mock import Mock
        starting = Mock(starting=True); starting.coming_week.return_value = "(Calendar not configured)"
        ready = Mock(starting=False); ready.coming_week.return_value = "Family (CalDAV): Home\n\n(No upcoming events)"
        waits = []
        section = CalendarSectionGenerator(None)
        with patch('assistant.prompt.sections.CalendarSectionGenerator.CalendarAccess.current', side_effect=[starting, ready, ready]):
            def wait(seconds):
                waits.append(seconds)
                if len(waits) == 2:
                    section._stop_event.set()
                return False
            section._wait = wait
            section._start_monitoring()
        assert waits == [CalendarSectionGenerator.STARTING_POLL_INTERVAL, CalendarSectionGenerator.UPDATE_INTERVAL]
        assert section.content.startswith("Family (CalDAV)")
