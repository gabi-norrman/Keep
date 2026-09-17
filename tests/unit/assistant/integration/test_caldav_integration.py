import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytz


def _config_mock():
    mock_config = MagicMock()
    mock_config.get_home_timezone.return_value = "Europe/Stockholm"
    return mock_config


class TestCalendarHelper:
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_returns_no_events_when_no_password(self, mock_config):
        mock_config.get.return_value = None
        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date.today(), date.today() + timedelta(days=5))
        assert result == "(Calendar not configured)"

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_returns_no_events_on_connection_error(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"
        mock_caldav.DAVClient.side_effect = ConnectionError("failed")
        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date.today(), date.today() + timedelta(days=5))
        assert result == "(No upcoming events)"

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_formats_same_day_event(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        # Create mock event
        local_tz = pytz.timezone('CET')
        start_dt = datetime(2025, 6, 15, 10, 0, tzinfo=pytz.UTC)
        end_dt = datetime(2025, 6, 15, 11, 0, tzinfo=pytz.UTC)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=start_dt)],
            'dtend': [Mock(value=end_dt)]
        }
        mock_vevent.summary = Mock(value="Team Meeting")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))

        assert "Team Meeting" in result
        # Same-day format: date time - time: title
        assert "2025-06-15" in result

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_formats_multi_day_event(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        start_dt = datetime(2025, 6, 15, 10, 0, tzinfo=pytz.UTC)
        end_dt = datetime(2025, 6, 17, 11, 0, tzinfo=pytz.UTC)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=start_dt)],
            'dtend': [Mock(value=end_dt)]
        }
        mock_vevent.summary = Mock(value="Conference")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))

        assert "Conference" in result
        # Multi-day format should include both dates
        assert "2025-06-15" in result
        assert "2025-06-17" in result

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_sorts_events_chronologically(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        event1_start = datetime(2025, 6, 15, 14, 0, tzinfo=pytz.UTC)
        event1_end = datetime(2025, 6, 15, 15, 0, tzinfo=pytz.UTC)
        event2_start = datetime(2025, 6, 15, 10, 0, tzinfo=pytz.UTC)
        event2_end = datetime(2025, 6, 15, 11, 0, tzinfo=pytz.UTC)

        def make_event(start, end, title):
            mock = Mock()
            mock.vobject_instance.vevent.contents = {
                'dtstart': [Mock(value=start)],
                'dtend': [Mock(value=end)]
            }
            mock.vobject_instance.vevent.summary = Mock(value=title)
            return mock

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [
            make_event(event1_start, event1_end, "Afternoon"),
            make_event(event2_start, event2_end, "Morning"),
        ]

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))

        morning_pos = result.index("Morning")
        afternoon_pos = result.index("Afternoon")
        assert morning_pos < afternoon_pos

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_empty_calendar_returns_no_events(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = []

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert result == "(No upcoming events)"

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_extract_time_with_string_value(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value="2025-06-15T10:00:00Z")],
            'dtend': [Mock(value="2025-06-15T11:00:00Z")]
        }
        mock_vevent.summary = Mock(value="String Time Event")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert "String Time Event" in result

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_plain_date_converted_to_datetime(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=date(2025, 6, 15))],
            'dtend': [Mock(value=date(2025, 6, 16))]
        }
        mock_vevent.summary = Mock(value="All Day Event")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert "All Day Event" in result

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_naive_datetime_gets_utc(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        naive_start = datetime(2025, 6, 15, 10, 0)
        naive_end = datetime(2025, 6, 15, 11, 0)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=naive_start)],
            'dtend': [Mock(value=naive_end)]
        }
        mock_vevent.summary = Mock(value="Naive DT Event")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert "Naive DT Event" in result

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_newline_in_title_replaced_with_space(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        start_dt = datetime(2025, 6, 15, 10, 0, tzinfo=pytz.UTC)
        end_dt = datetime(2025, 6, 15, 11, 0, tzinfo=pytz.UTC)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=start_dt)],
            'dtend': [Mock(value=end_dt)]
        }
        mock_vevent.summary = Mock(value="Line1\nLine2")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert "Line1 Line2" in result
        assert "\n" not in result.split(": ", 1)[-1]

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_multi_calendar_events_merged_and_sorted(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        def make_event(start, end, title):
            mock = Mock()
            mock.vobject_instance.vevent.contents = {
                'dtstart': [Mock(value=start)],
                'dtend': [Mock(value=end)]
            }
            mock.vobject_instance.vevent.summary = Mock(value=title)
            return mock

        cal1 = Mock()
        cal1.date_search.return_value = [
            make_event(datetime(2025, 6, 15, 14, 0, tzinfo=pytz.UTC),
                       datetime(2025, 6, 15, 15, 0, tzinfo=pytz.UTC), "Cal1 Afternoon")
        ]
        cal2 = Mock()
        cal2.date_search.return_value = [
            make_event(datetime(2025, 6, 15, 9, 0, tzinfo=pytz.UTC),
                       datetime(2025, 6, 15, 10, 0, tzinfo=pytz.UTC), "Cal2 Morning")
        ]

        mock_principal = Mock()
        mock_principal.calendars.return_value = [cal1, cal2]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))

        assert "Cal2 Morning" in result
        assert "Cal1 Afternoon" in result
        assert result.index("Cal2 Morning") < result.index("Cal1 Afternoon")

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_event_end_time_in_range_included(self, mock_config, mock_caldav):
        mock_config.get.return_value = "password123"

        start_dt = datetime(2025, 6, 14, 22, 0, tzinfo=pytz.UTC)
        end_dt = datetime(2025, 6, 15, 2, 0, tzinfo=pytz.UTC)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=start_dt)],
            'dtend': [Mock(value=end_dt)]
        }
        mock_vevent.summary = Mock(value="Overnight Event")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 6, 15), date(2025, 6, 20))
        assert "Overnight Event" in result


    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_summer_event_uses_cest_not_cet(self, mock_config, mock_caldav):
        """Europe/Stockholm should show CEST (UTC+2) in summer, not CET (UTC+1).
        Regression test: using 'CET' instead of 'Europe/Stockholm' would show 11:00 instead of 12:00.
        """
        mock_config.get.return_value = "password123"

        # July 15 at 10:00 UTC -> should be 12:00 CEST (UTC+2)
        start_dt = datetime(2025, 7, 15, 10, 0, tzinfo=pytz.UTC)
        end_dt = datetime(2025, 7, 15, 11, 0, tzinfo=pytz.UTC)

        mock_event = Mock()
        mock_vevent = Mock()
        mock_vevent.contents = {
            'dtstart': [Mock(value=start_dt)],
            'dtend': [Mock(value=end_dt)]
        }
        mock_vevent.summary = Mock(value="Summer Meeting")
        mock_event.vobject_instance.vevent = mock_vevent

        mock_calendar = Mock()
        mock_calendar.date_search.return_value = [mock_event]
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import fetch_calendar_events
        result = fetch_calendar_events(date(2025, 7, 15), date(2025, 7, 20))

        assert "12:00" in result, f"Expected CEST (12:00) but got: {result}"
        assert "13:00" in result, f"Expected CEST end time (13:00) but got: {result}"

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_create_event_summer_uses_cest(self, mock_config, mock_caldav):
        """create_calendar_event should localize summer dates to CEST (UTC+2)."""
        mock_config.get.side_effect = lambda key, *args: {
            'calendar.username': 'user@me.com',
            'calendar.password': 'password123',
        }.get(key, args[0] if args else None)

        mock_calendar = Mock()
        mock_calendar.name = 'Home'
        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        create_calendar_event("Summer Event", "2025-07-15", "10:00", "11:00")

        call_kwargs = mock_calendar.save_event.call_args[1]
        assert call_kwargs['dtstart'].utcoffset() == timedelta(hours=2)


class TestIcalendarDependencyCompatibility:
    """Guards the icalendar/recurring-ical-events pins that caldav 1.3.9 needs.

    These exercise the *real* installed libraries (no mocking), so they fail if
    icalendar is bumped to 6.0+ (component_factory becomes a non-subscriptable
    module, breaking event creation) or recurring-ical-events to 3.x (which then
    requires icalendar>=6.1, breaking recurrence expansion on the read path).
    """

    def test_component_factory_is_subscriptable(self):
        import icalendar
        # caldav/lib/vcal.py does icalendar.cal.component_factory["VEVENT"]()
        component = icalendar.cal.component_factory["VEVENT"]()
        assert component is not None

    def test_recurring_ical_events_imports(self):
        import recurring_ical_events
        assert hasattr(recurring_ical_events, "of")


class TestCreateCalendarEvent:
    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_happy_path_creates_event(self, mock_config, mock_caldav):
        mock_config.get.side_effect = lambda key, *args: {
            'calendar.username': 'user@me.com',
            'calendar.password': 'password123',
        }.get(key, args[0] if args else None)

        mock_calendar = Mock()
        mock_calendar.name = 'Home'

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        create_calendar_event("Dentist", "2025-10-28", "10:00", "11:00")

        mock_calendar.save_event.assert_called_once()
        call_kwargs = mock_calendar.save_event.call_args[1]
        assert call_kwargs['summary'] == "Dentist"
        assert call_kwargs['dtstart'].hour == 10
        assert call_kwargs['dtend'].hour == 11
        assert call_kwargs['dtstart'].tzinfo is not None
        assert call_kwargs['dtend'].tzinfo is not None

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_calendar_not_found_raises(self, mock_config, mock_caldav):
        mock_config.get.side_effect = lambda key, *args: {
            'calendar.username': 'user@me.com',
            'calendar.password': 'password123',
        }.get(key, args[0] if args else None)

        mock_calendar = Mock()
        mock_calendar.name = 'Work'

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        with pytest.raises(ValueError, match="Calendar 'Home' not found"):
            create_calendar_event("Dentist", "2025-10-28", "10:00", "11:00")

    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_no_password_raises(self, mock_config):
        mock_config.get.return_value = None

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        with pytest.raises(ValueError, match="username or password not found"):
            create_calendar_event("Dentist", "2025-10-28", "10:00", "11:00")

    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_invalid_date_format_raises(self, mock_config):
        mock_config.get.return_value = 'password123'

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        with pytest.raises(ValueError):
            create_calendar_event("Dentist", "bad-date", "10:00", "11:00")

    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_invalid_time_format_raises(self, mock_config):
        mock_config.get.return_value = 'password123'

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        with pytest.raises(ValueError):
            create_calendar_event("Dentist", "2025-10-28", "not-a-time", "11:00")

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_caldav_error_propagates(self, mock_config, mock_caldav):
        mock_config.get.side_effect = lambda key, *args: {
            'calendar.username': 'user@me.com',
            'calendar.password': 'password123',
        }.get(key, args[0] if args else None)

        mock_calendar = Mock()
        mock_calendar.name = 'Home'
        mock_calendar.save_event.side_effect = Exception("CalDAV connection failed")

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        with pytest.raises(Exception, match="CalDAV connection failed"):
            create_calendar_event("Dentist", "2025-10-28", "10:00", "11:00")

    @patch('assistant.integration.integrations.CalDAVIntegration.caldav')
    @patch('assistant.integration.integrations.CalDAVIntegration.config', new_callable=lambda: _config_mock())
    def test_uses_configured_calendar_name(self, mock_config, mock_caldav):
        mock_config.get.side_effect = lambda key, *args: {
            'calendar.username': 'user@me.com',
            'calendar.password': 'password123',
            'calendar.default_calendar': 'Family',
        }.get(key, args[0] if args else None)

        mock_calendar = Mock()
        mock_calendar.name = 'Family'

        mock_principal = Mock()
        mock_principal.calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal
        mock_caldav.DAVClient.return_value = mock_client

        from assistant.integration.integrations.CalDAVIntegration import create_calendar_event
        create_calendar_event("Dentist", "2025-10-28", "10:00", "11:00")

        mock_calendar.save_event.assert_called_once()
