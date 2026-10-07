"""CalendarAccess: the accounts of every enabled and configured CalendarSource as one calendar —
the week's header and events, loading named calendars, creating an event, one account failing,
and no account at all."""
from datetime import date, datetime, timedelta
from typing import List
from unittest.mock import MagicMock, patch

import pytest
import pytz

from assistant.integration.Integration import Integration, IntegrationState
from assistant.integration.IntegrationMonitor import IntegrationMonitor
from assistant.integration.calendar.CalendarAccess import CalendarAccess, EVENTS_UNAVAILABLE, NOT_CONFIGURED, NO_ACCOUNT_HINT
from assistant.integration.calendar.CalendarAccount import CalendarAccount, CalendarInfo, split_names
from assistant.integration.calendar.CalendarSource import CalendarSource
from assistant.integration.calendar.Event import Event, NO_EVENTS

UTC = pytz.UTC
WEEK = (date(2025, 6, 15), date(2025, 6, 20))


@pytest.fixture(autouse=True)
def stockholm():
    config = MagicMock()
    config.get_home_timezone.return_value = "Europe/Stockholm"
    with patch("assistant.integration.calendar.Event.config", new=config), \
            patch("assistant.integration.calendar.CalendarAccess.config", new=config):
        yield


class _Account(CalendarAccount):
    """A calendar account over dicts: `calendars` maps a name to its events (None for a
    Reminders list), `created` records what create_event was handed."""

    def __init__(self, name="iCloud", calendars=None, shown=(), default="Home", failing=False):
        self._name = name
        self._calendars = calendars or {}
        self._shown = list(shown)
        self._default = default
        self._failing = failing
        self.created: List[tuple] = []
        self.asked: List[list] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def shown_calendars(self) -> list[str]:
        return self._shown

    @property
    def default_calendar(self) -> str:
        return self._default

    def calendars(self) -> list[CalendarInfo]:
        if self._failing:
            raise ConnectionError("failed")
        return [CalendarInfo(name, events is not None) for name, events in self._calendars.items()]

    def events(self, calendar_names, start, end):
        self.asked.append(list(calendar_names))
        return [e for name in calendar_names for e in (self._calendars.get(name) or [])]

    def create_event(self, calendar_name, event):
        self.created.append((calendar_name, event))

    def check(self):
        return True


def _event(title, day, hour, calendar="Home"):
    return Event(datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC),
                 datetime(day.year, day.month, day.day, hour + 1, 0, tzinfo=UTC), title, calendar=calendar)


def _household(**kwargs):
    day = date(2025, 6, 15)
    return _Account(calendars={"Home": [_event("Dentist Charlotte", day, 9)],
                               "Family": [_event("Dinner at Ingeborg's", day, 17, "Family")],
                               "Work": [_event("Budget review", day, 13, "Work")],
                               "Shopping": None}, **kwargs)


@pytest.mark.unit
class TestFetch:
    def test_only_the_shown_calendars_have_their_events_listed(self):
        text = CalendarAccess([_household(shown=["Home", "family"])]).fetch(*WEEK)
        assert "Dentist Charlotte" in text and "Dinner at Ingeborg's" in text
        assert "Budget review" not in text

    def test_the_calendars_left_out_are_named_with_the_command_that_loads_them(self):
        shown, left_out = CalendarAccess([_household(shown=["Home", "Family"])]).fetch(*WEEK).split("\n\n")[0].split("\n")
        assert "Home" in shown and "Family" in shown and "Work" not in shown
        assert "load_calendar" in left_out and "Work" in left_out
        assert "Home" not in left_out and "Family" not in left_out

    def test_a_reminders_list_is_searched_but_never_named(self):
        account = _household(shown=["Home"])
        text = CalendarAccess([account]).fetch(*WEEK)
        assert "Shopping" not in text
        account_without_shown = _household()
        CalendarAccess([account_without_shown]).fetch(*WEEK)
        assert "Shopping" in account_without_shown.asked[0] and "Shopping" not in CalendarAccess([account_without_shown]).fetch(*WEEK)

    def test_without_the_setting_every_calendar_is_shown_and_none_is_listed(self):
        text = CalendarAccess([_household()]).fetch(*WEEK)
        assert "Dentist Charlotte" in text and "Budget review" in text
        assert "load_calendar" not in text

    def test_a_shown_name_the_account_lacks_shows_nothing_but_names_the_rest(self):
        text = CalendarAccess([_household(shown=["Nope"])]).fetch(*WEEK)
        assert text.startswith("Calendars shown below: none")
        assert "Work" in text and text.endswith(NO_EVENTS)

    def test_events_of_several_accounts_are_one_list_in_time_order(self):
        day = date(2025, 6, 16)
        first = _Account("Family", {"Home": [_event("Late", day, 15)]})
        second = _Account("Google", {"Work": [_event("Early", day, 8, "Work")]})

        text = CalendarAccess([first, second]).fetch(*WEEK)

        assert text.index("Early") < text.index("Late")
        header = text.split("\n\n")[0]
        assert "Family" in header and "Google" in header

    def test_one_failing_account_is_named_while_the_others_still_show(self):
        broken = _Account("Broken", failing=True)
        text = CalendarAccess([_household(), broken]).fetch(*WEEK)
        assert "(Broken: calendar could not be loaded)" in text
        assert "Dentist Charlotte" in text

    def test_every_account_failing_is_unavailable_not_an_empty_week(self):
        text = CalendarAccess([_Account(failing=True)]).fetch(*WEEK)
        assert text == EVENTS_UNAVAILABLE and text != NO_EVENTS

    def test_no_account_is_not_configured(self):
        assert CalendarAccess([]).fetch(*WEEK) == NOT_CONFIGURED

    def test_the_coming_week_starts_today_and_covers_eight_days(self):
        account = _Account()
        with patch.object(CalendarAccess, "fetch", return_value="") as fetch:
            CalendarAccess([account]).coming_week()
        start, end = fetch.call_args.args
        assert start == date.today() and (end - start).days == 8


@pytest.mark.unit
class TestLoad:
    def test_loads_only_the_named_calendar_whatever_its_case(self):
        tomorrow = date.today() + timedelta(days=1)
        account = _Account(calendars={"Home": [_event("Dentist Charlotte", tomorrow, 9)],
                                      "Work": [_event("Budget review", tomorrow, 13, "Work")]})

        text = CalendarAccess([account]).load(["work"])

        assert "Budget review" in text and "Dentist Charlotte" not in text
        assert text.startswith("Calendar 'Work', ")

    def test_an_unknown_name_fails_and_says_which_calendars_exist(self):
        tomorrow = date.today() + timedelta(days=1)
        account = _Account(calendars={"Home": [], "Work": [_event("Budget review", tomorrow, 13, "Work")], "Shopping": None})

        text = CalendarAccess([account]).load(["School", "'Work'"])

        assert "failed" in text and "Home" in text and "Shopping" not in text.split("\n\n")[0]
        assert "Budget review" in text

    def test_a_calendar_of_a_second_account_is_found_too(self):
        tomorrow = date.today() + timedelta(days=1)
        second = _Account("Google", {"Kids": [_event("Football", tomorrow, 16, "Kids")]})

        text = CalendarAccess([_Account(), second]).load(["kids"])

        assert "Football" in text

    def test_without_an_account_loading_raises_the_hint(self):
        with pytest.raises(ValueError, match="list_integrations"):
            CalendarAccess([]).load(["Home"])


@pytest.mark.unit
class TestCreate:
    def test_the_event_lands_in_the_first_accounts_default_calendar_in_local_time(self):
        account = _household(default="Family")

        CalendarAccess([account]).create("Dentist", "2025-07-15", "10:00", "11:00")

        (calendar_name, event), = account.created
        assert calendar_name == "Family" and event.title == "Dentist"
        assert event.start.utcoffset() == timedelta(hours=2)   # CEST in July, not CET
        assert (event.start.hour, event.end.hour) == (10, 11)

    def test_a_missing_default_calendar_raises_and_names_the_available_ones(self):
        with pytest.raises(ValueError, match="Calendar 'Nope' not found") as raised:
            CalendarAccess([_household(default="Nope")]).create("Dentist", "2025-10-28", "10:00", "11:00")
        assert "Home" in str(raised.value)

    def test_without_an_account_creating_raises_the_hint(self):
        with pytest.raises(ValueError, match=NO_ACCOUNT_HINT[:20]):
            CalendarAccess([]).create("Dentist", "2025-10-28", "10:00", "11:00")


@pytest.mark.unit
class TestSplitNames:
    def test_a_list_a_string_and_a_comma_joined_item_all_name_each_calendar(self):
        assert split_names(["Home", "Family"]) == ["Home", "Family"]
        assert split_names("Home, Work") == ["Home", "Work"]
        assert split_names(["Home, Family", "Work"]) == ["Home", "Family", "Work"]

    def test_anything_else_is_no_names(self):
        assert split_names(None) == [] and split_names(42) == [] and split_names([]) == []


class _Source(Integration, CalendarSource):
    def __init__(self, name: str, keys: List[str], accounts):
        super().__init__()
        self._name, self._keys, self._accounts = name, keys, accounts

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_keys(self) -> List[str]:
        return self._keys

    def check_health(self) -> bool:
        return True

    def calendar_accounts(self):
        return self._accounts


@pytest.mark.unit
class TestGatheringThroughTheMonitor:
    @pytest.fixture(autouse=True)
    def monitor(self):
        IntegrationMonitor.reset_instance()
        yield IntegrationMonitor.get_instance()
        IntegrationMonitor.reset_instance()

    def test_configured_sources_contribute_and_unconfigured_ones_do_not(self, monitor):
        icloud, google = _Account("iCloud"), _Account("Google")
        unconfigured = _Source("Google", ["fictional_calendar_section_nobody_has"], [google])
        monitor.register(_Source("CalDAV", [], [icloud]))
        monitor.register(unconfigured)

        assert unconfigured.state is IntegrationState.NOT_CONFIGURED
        assert CalendarAccess.current().accounts == [icloud]

    def test_nothing_registered_is_no_account(self, monitor):
        assert CalendarAccess.current().accounts == []
        assert CalendarAccess.current().coming_week() == NOT_CONFIGURED
