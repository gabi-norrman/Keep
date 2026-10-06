from unittest.mock import patch
from datetime import date


class TestCalendarDataSectionGenerator:
    @patch('integrations.caldav.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_uses_8_day_range(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from integrations.caldav.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._fetch_content()
        args = mock_cls.fetch_calendar_events.call_args[0]
        start, end = args
        assert (end - start).days == 8

    @patch('integrations.caldav.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_start_date_is_today(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from integrations.caldav.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._fetch_content()
        start = mock_cls.fetch_calendar_events.call_args[0][0]
        assert start == date.today()
