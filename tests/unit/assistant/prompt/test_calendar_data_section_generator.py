import pytest
from unittest.mock import patch, MagicMock
from datetime import date


class TestCalendarDataSectionGenerator:
    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_calls_calendar(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "2025-01-01 10:00 - 11:00: Meeting"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        result = section._fetch_content()
        mock_cls.fetch_calendar_events.assert_called_once()
        assert "Meeting" in result

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_uses_8_day_range(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._fetch_content()
        args = mock_cls.fetch_calendar_events.call_args[0]
        start, end = args
        assert (end - start).days == 8

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_returns_no_upcoming_events(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        result = section._fetch_content()
        assert result == "(No upcoming events)"

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_returns_multiline_events(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "2025-01-01 10:00 - 11:00: Meeting\n2025-01-02 14:00 - 15:00: Dentist"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        result = section._fetch_content()
        assert "Meeting" in result
        assert "Dentist" in result

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_start_date_is_today(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._fetch_content()
        start = mock_cls.fetch_calendar_events.call_args[0][0]
        assert start == date.today()

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_propagates_exception(self, mock_cls):
        mock_cls.fetch_calendar_events.side_effect = ConnectionError("network down")
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        with pytest.raises(ConnectionError, match="network down"):
            section._fetch_content()

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_returns_empty_string(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = ""
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        result = section._fetch_content()
        assert result == ""

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_fetch_content_returns_unicode_events(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "2025-01-01 10:00 - 11:00: Möte med Björk"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        result = section._fetch_content()
        assert "Björk" in result

    def test_section_name_is_calendar_data(self):
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        assert section.section_name == "CalendarData"

    @patch('assistant.prompt.PromptSectionGenerator.PromptSectionGenerator._wait', side_effect=StopIteration)
    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_monitoring_calls_update_then_sleeps(self, mock_cls, mock_sleep):
        mock_cls.fetch_calendar_events.return_value = "(No upcoming events)"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        try:
            section._start_monitoring()
        except StopIteration:
            pass
        mock_cls.fetch_calendar_events.assert_called_once()
        mock_sleep.assert_called_once_with(15 * 60)

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_update_content_sets_content_and_timestamp(self, mock_cls):
        import time
        mock_cls.fetch_calendar_events.return_value = "2025-06-01 09:00 - 10:00: Standup"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        before = time.time()
        section._update_content()
        assert section.content == "2025-06-01 09:00 - 10:00: Standup"
        assert section.last_update_time >= before

    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_update_content_notifies_prompt_manager(self, mock_cls):
        mock_cls.fetch_calendar_events.return_value = "event"
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        mock_pm = MagicMock()
        section = CalendarDataSectionGenerator(mock_pm)
        section._update_content()
        mock_pm.on_section_updated.assert_called_once_with("CalendarData")

    @patch('assistant.prompt.PromptSectionGenerator.time.sleep')
    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_update_content_retries_on_failure_then_succeeds(self, mock_cls, mock_sleep):
        mock_cls.fetch_calendar_events.side_effect = [ConnectionError("fail"), "2025-01-01 10:00 - 11:00: Retry OK"]
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._update_content()
        assert section.content == "2025-01-01 10:00 - 11:00: Retry OK"
        assert mock_cls.fetch_calendar_events.call_count == 2

    @patch('assistant.prompt.PromptSectionGenerator.time.sleep')
    @patch('assistant.prompt.sections.CalendarDataSectionGenerator.CalDAVIntegration')
    def test_update_content_sets_error_after_max_retries(self, mock_cls, mock_sleep):
        mock_cls.fetch_calendar_events.side_effect = ConnectionError("persistent failure")
        from assistant.prompt.sections.CalendarDataSectionGenerator import CalendarDataSectionGenerator
        section = CalendarDataSectionGenerator(None)
        section._update_content()
        assert "ERROR" in section.content
        assert "persistent failure" in section.content
        assert mock_cls.fetch_calendar_events.call_count == 3
