"""The CalDAV folder: the account that turns the library's calendars and vobject events into
CalendarInfo and Event, the integration that builds one per `caldav.accounts` entry, and the
icalendar pins the library needs."""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
import pytz

from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.integration.calendar.Event import Event
from integrations.caldav.CalDavCalendarAccount import CalDavCalendarAccount

CALDAV = 'integrations.caldav.CalDavCalendarAccount.caldav'
CONFIG = 'integrations.caldav.CalDAVIntegration.ConfigManager.get_instance'
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


def _config(accounts):
    """ConfigManager.get as the integration reads it, serving `caldav.accounts`."""
    config = MagicMock()
    config.get.side_effect = lambda key, *default: accounts if key == "caldav.accounts" else (default[0] if default else None)
    return patch(CONFIG, return_value=config)


@pytest.mark.unit
class TestCalDAVIntegration:
    @pytest.fixture(autouse=True)
    def fresh(self):
        from integrations.caldav.CalDAVIntegration import CalDAVIntegration
        CalDAVIntegration.reset_instance()
        yield
        CalDAVIntegration.reset_instance()

    def _integration(self):
        from integrations.caldav.CalDAVIntegration import CalDAVIntegration
        return CalDAVIntegration()

    def test_builds_one_account_per_entry_the_icloud_preset_giving_the_url_a_contract_with_caldav_accounts(self):
        entries = [{"name": "Family", "preset": "icloud", "username": "m@mlab.test", "password": "app-password",
                    "shown_calendars": ["Home, Family", "Work"], "default_calendar": "Family"},
                   {"name": "Work", "url": "https://caldav.example/", "username": "marcus@example.com", "password": "pw"}]
        with _config(entries):
            integration = self._integration()
            family, work = integration.calendar_accounts()
            assert isinstance(integration, CalendarSource) and integration.config_keys == ["caldav.accounts"]
            assert family.name == "Family" and family._url == "https://caldav.icloud.com/" and family.service == "CalDAV"
            assert family._credentials() == ("m@mlab.test", "app-password")
            assert family.shown_calendars == ["Home", "Family", "Work"] and family.default_calendar == "Family"
            assert work._url == "https://caldav.example/" and work._credentials() == ("marcus@example.com", "pw")
            assert work.shown_calendars == [] and work.default_calendar == "Home"

    def test_the_credentials_are_read_from_config_at_call_time(self):
        entries = [{"name": "Family", "preset": "icloud", "username": "m@mlab.test", "password": "old"}]
        with _config(entries):
            account, = self._integration().calendar_accounts()
            entries[0]["password"] = "new"
            assert account._credentials() == ("m@mlab.test", "new")

    def test_an_entry_without_a_preset_or_url_an_unknown_preset_or_a_repeated_name_is_skipped(self):
        entries = [{"name": "Family", "username": "m@mlab.test", "password": "pw"},
                   {"name": "Work", "preset": "exchange", "username": "x", "password": "y"},
                   {"name": "Kids", "preset": "icloud", "username": "k@example.com", "password": "pw"},
                   {"name": "kids", "preset": "icloud", "username": "other@example.com", "password": "pw"}]
        with _config(entries):
            accounts = self._integration().calendar_accounts()
        assert [a.name for a in accounts] == ["Kids"]

    def test_the_example_config_lists_shown_calendars_as_plain_values_so_config_set_can_add_one_by_index(self):
        from common import ConfigSchema
        assert ConfigSchema.is_scalar_list("caldav.accounts[0].shown_calendars")
        assert ConfigSchema.example_value("caldav.accounts[0].shown_calendars") == []

    def test_health_checks_every_account_and_names_the_failing_ones(self):
        entries = [{"name": "Family", "preset": "icloud", "username": "a", "password": "b"},
                   {"name": "Work", "preset": "icloud", "username": "c", "password": "d"}]
        with _config(entries):
            integration = self._integration()
        family, work = integration.calendar_accounts()
        with patch.object(family, "check", return_value=True), patch.object(work, "check", side_effect=ConnectionError("down")):
            with pytest.raises(RuntimeError) as raised:
                integration.check_health()
        assert "Work" in str(raised.value) and "down" in str(raised.value) and "Family" not in str(raised.value)
        assert [(a["name"], a["state"]) for a in integration.accounts_status()] == [("Family", "ok"), ("Work", "error")]
        with patch.object(family, "check", return_value=True), patch.object(work, "check", return_value=True):
            assert integration.check_health() is True
        assert all(a["state"] == "ok" for a in integration.accounts_status())

    def test_without_an_account_health_fails_and_the_status_is_empty(self):
        with _config([]):
            integration = self._integration()
            assert integration.check_health() is False and integration.accounts_status() == []


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
