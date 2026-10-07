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
