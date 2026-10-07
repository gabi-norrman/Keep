"""The CalDAV folder: the account that turns the library's calendars and vobject events into
CalendarInfo and Event, the integration that builds it from the `calendar` section, and the
icalendar pins the library needs."""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
import pytz

from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.integration.calendar.Event import Event
from integrations.caldav.CalDavCalendarAccount import CalDavCalendarAccount

CALDAV = 'integrations.caldav.CalDavCalendarAccount.caldav'
CONFIG = 'integrations.caldav.CalDAVIntegration.config'
UTC = pytz.UTC


def _calendar(name, *events, components=("VEVENT",), day=date(2025, 6, 15)):
    """A library calendar holding `(title, start)` events on `day`; a start may be an hour, a
    vobject string, a plain date or a naive datetime."""
    calendar = Mock()
    calendar.name = name
    if components is None:
        calendar.get_supported_components.side_effect = RuntimeError("no props")
    else:
        calendar.get_supported_components.return_value = list(components)
    found = []
    for title, start in events:
        if isinstance(start, int):
            start, end = (datetime(day.year, day.month, day.day, start, 0, tzinfo=UTC),
                          datetime(day.year, day.month, day.day, start + 1, 0, tzinfo=UTC))
        else:
            start, end = start
        vevent = Mock()
        vevent.contents = {'dtstart': [Mock(value=start)], 'dtend': [Mock(value=end)]}
        vevent.summary = Mock(value=title)
        event = Mock()
        event.vobject_instance.vevent = vevent
        found.append(event)
    calendar.date_search.return_value = found
    return calendar


def _serve(mock_caldav, *calendars):
    mock_caldav.DAVClient.return_value.principal.return_value.calendars.return_value = list(calendars)


def _account(shown=(), default="Home", username="m@mlab.test", password="app-password"):
    return CalDavCalendarAccount("iCloud", "https://caldav.example/", lambda: (username, password),
                                 lambda: list(shown), lambda: default)


@pytest.mark.unit
class TestCalendars:
    @patch(CALDAV)
    def test_names_every_calendar_and_marks_a_reminders_list(self, mock_caldav):
        _serve(mock_caldav, _calendar("Home"), _calendar("Shopping", components=("VTODO",)), _calendar("Mystery", components=None))

        infos = _account().calendars()

        assert [(c.name, c.holds_events) for c in infos] == [("Home", True), ("Shopping", False), ("Mystery", True)]
        mock_caldav.DAVClient.assert_called_once_with("https://caldav.example/", username="m@mlab.test", password="app-password")

    def test_missing_credentials_raise_before_any_connection(self):
        with patch(CALDAV) as mock_caldav:
            with pytest.raises(ValueError):
                _account(password="").calendars()
            mock_caldav.DAVClient.assert_not_called()


@pytest.mark.unit
class TestEvents:
    @patch(CALDAV)
    def test_only_the_named_calendars_are_searched_whatever_their_case(self, mock_caldav):
        home, work = _calendar("Home", ("Dentist Charlotte", 9)), _calendar("Work", ("Budget review", 13))
        _serve(mock_caldav, home, work)

        events = _account().events(["work"], date(2025, 6, 15), date(2025, 6, 20))

        assert [e.title for e in events] == ["Budget review"]
        assert events[0].calendar == "Work" and events[0].account == "iCloud"
        home.date_search.assert_not_called()
        work.date_search.assert_called_once_with(start=datetime(2025, 6, 15), end=datetime(2025, 6, 20))

    @patch(CALDAV)
    def test_the_calendars_of_the_last_listing_are_reused_without_a_second_lookup(self, mock_caldav):
        _serve(mock_caldav, _calendar("Home", ("Dentist", 9)))
        account = _account()

        account.calendars()
        account.events(["Home"], date(2025, 6, 15), date(2025, 6, 20))

        assert mock_caldav.DAVClient.call_count == 1

    @patch(CALDAV)
    def test_a_vobject_string_time_is_utc(self, mock_caldav):
        _serve(mock_caldav, _calendar("Home", ("String Time", ("2025-06-15T10:00:00Z", "2025-06-15T11:00:00Z"))))

        event, = _account().events(["Home"], date(2025, 6, 15), date(2025, 6, 20))

        assert event.start == datetime(2025, 6, 15, 10, 0, tzinfo=UTC) and not event.all_day

    @patch(CALDAV)
    def test_a_plain_date_is_an_all_day_event_at_midnight_utc(self, mock_caldav):
        _serve(mock_caldav, _calendar("Home", ("All Day", (date(2025, 6, 15), date(2025, 6, 16)))))

        event, = _account().events(["Home"], date(2025, 6, 15), date(2025, 6, 20))

        assert event.all_day
        assert (event.start, event.end) == (datetime(2025, 6, 15, tzinfo=UTC), datetime(2025, 6, 16, tzinfo=UTC))

    @patch(CALDAV)
    def test_a_naive_datetime_is_taken_as_utc(self, mock_caldav):
        _serve(mock_caldav, _calendar("Home", ("Naive", (datetime(2025, 6, 15, 10, 0), datetime(2025, 6, 15, 11, 0)))))

        event, = _account().events(["Home"], date(2025, 6, 15), date(2025, 6, 20))

        assert event.start.tzinfo is not None and event.start.utcoffset() == timedelta(0)


@pytest.mark.unit
class TestCreateEvent:
    def _event(self, title="Dentist"):
        tz = pytz.timezone("Europe/Stockholm")
        return Event(tz.localize(datetime(2025, 10, 28, 10, 0)), tz.localize(datetime(2025, 10, 28, 11, 0)), title)

    @patch(CALDAV)
    def test_saves_the_event_in_the_named_calendar(self, mock_caldav):
        home, family = _calendar("Home"), _calendar("Family")
        _serve(mock_caldav, home, family)
        event = self._event()

        _account().create_event("Family", event)

        family.save_event.assert_called_once_with(dtstart=event.start, dtend=event.end, summary="Dentist")
        home.save_event.assert_not_called()

    @patch(CALDAV)
    def test_an_unknown_calendar_raises_and_names_the_available_ones(self, mock_caldav):
        _serve(mock_caldav, _calendar("Work"))

        with pytest.raises(ValueError, match="Calendar 'Home' not found") as raised:
            _account().create_event("Home", self._event())
        assert "Work" in str(raised.value)


@pytest.mark.unit
class TestCheck:
    @patch(CALDAV)
    def test_reads_today_from_the_first_calendar(self, mock_caldav):
        home = _calendar("Home")
        _serve(mock_caldav, home)

        assert _account().check() is True

        start, end = home.date_search.call_args.kwargs["start"], home.date_search.call_args.kwargs["end"]
        assert start.date() == date.today() and (end - start).days == 1

    @patch(CALDAV)
    def test_a_connection_failure_raises_for_the_monitor(self, mock_caldav):
        mock_caldav.DAVClient.side_effect = ConnectionError("failed")
        with pytest.raises(ConnectionError):
            _account().check()


def _config_mock(values):
    config = MagicMock()
    config.get.side_effect = lambda key, *default: values[key] if key in values else (default[0] if default else None)
    return config


@pytest.mark.unit
class TestCalDAVIntegration:
    def _integration(self):
        from integrations.caldav.CalDAVIntegration import CalDAVIntegration
        return CalDAVIntegration()

    def test_is_a_calendar_source_with_one_icloud_account_read_from_the_calendar_section(self):
        values = {"calendar.username": "m@mlab.test", "calendar.password": "app-password",
                  "calendar.shown_calendars": ["Home, Family", "Work"], "calendar.default_calendar": "Family"}
        with patch(CONFIG, new=_config_mock(values)):
            integration = self._integration()
            account, = integration.calendar_accounts()
            assert isinstance(integration, CalendarSource) and integration.config_keys == ["calendar"]
            assert account.name == "iCloud" and account._url == "https://caldav.icloud.com/"
            assert account._credentials() == ("m@mlab.test", "app-password")
            assert account.shown_calendars == ["Home", "Family", "Work"]
            assert account.default_calendar == "Family"

    def test_the_default_calendar_is_home_and_no_shown_setting_means_no_names(self, tmp_path, monkeypatch):
        """Through the real ConfigManager.get: a stub returning None for the absent key hid the KeyError it raises."""
        from common.ConfigManager import ConfigManager
        path = tmp_path / "config.yaml"
        path.write_text("calendar:\n  username: m@mlab.test\n  password: app-password\n"
                        "location:\n  home:\n    timezone: Europe/Stockholm\n")
        monkeypatch.setenv("FLOXY_CONFIG", str(path))
        monkeypatch.setattr(CONFIG, ConfigManager())

        account, = self._integration().calendar_accounts()

        assert account.shown_calendars == [] and account.default_calendar == "Home"

    def test_the_example_config_lists_shown_calendars_as_plain_values_so_config_set_can_add_one_by_index(self):
        from common import ConfigSchema
        assert ConfigSchema.is_scalar_list("calendar.shown_calendars")
        assert ConfigSchema.example_value("calendar.shown_calendars") == []

    def test_health_fails_without_credentials_and_is_the_accounts_check_with_them(self):
        with patch(CONFIG, new=_config_mock({"calendar.username": "", "calendar.password": ""})):
            assert self._integration().check_health() is False
        with patch(CONFIG, new=_config_mock({"calendar.username": "m@mlab.test", "calendar.password": "pw"})):
            integration = self._integration()
            with patch.object(integration._account, "check", return_value=True) as check:
                assert integration.check_health() is True
            check.assert_called_once()


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
