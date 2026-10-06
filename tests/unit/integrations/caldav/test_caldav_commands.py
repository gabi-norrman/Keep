from unittest.mock import patch


class TestCmdCreateEvent:
    def test_run_create_event_title_with_commas(self, commands_instance):
        # Simulates LLM under-quoting: a title containing a comma got split into
        # multiple tokens at parse time. The handler re-joins leading tokens.
        with patch("integrations.caldav.CalDAVCommands.CalDAVIntegration.create_calendar_event") as mock_create:
            result = commands_instance._owner("create_event")._run_create_event(
                "Dinner with John", "Sarah", "2025-10-28", "18:00", "19:00"
            )
            mock_create.assert_called_once_with("Dinner with John, Sarah", "2025-10-28", "18:00", "19:00")
            assert result == ""

    def test_run_create_event_exception_from_calendar(self, commands_instance):
        with patch("integrations.caldav.CalDAVCommands.CalDAVIntegration.create_calendar_event", side_effect=ValueError("bad date")):
            result = commands_instance._owner("create_event")._run_create_event("Dentist", "bad-date", "10:00", "11:00")
            assert "create_event failed" in result
            assert "bad date" in result
