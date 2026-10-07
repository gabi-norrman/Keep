"""Event.render: the lines of the calendar section — the window, the order, the home timezone,
the same-day and multi-day forms."""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
import pytz

from assistant.integration.calendar.Event import Event, render, NO_EVENTS

UTC = pytz.UTC


@pytest.fixture(autouse=True)
def stockholm():
    config = MagicMock()
    config.get_home_timezone.return_value = "Europe/Stockholm"
    with patch("assistant.integration.calendar.Event.config", new=config):
        yield


def _event(title, start, end, **kwargs):
    return Event(start, end, title, **kwargs)


@pytest.mark.unit
class TestRender:
    def test_a_same_day_event_is_one_line_in_local_time(self):
        line = render([_event("Team Meeting", datetime(2025, 6, 15, 10, 0, tzinfo=UTC), datetime(2025, 6, 15, 11, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert line == "2025-06-15 12:00 - 13:00: Team Meeting"   # CEST, UTC+2 in June

    def test_winter_events_are_cet_not_cest(self):
        line = render([_event("Lunch", datetime(2025, 1, 15, 11, 0, tzinfo=UTC), datetime(2025, 1, 15, 12, 0, tzinfo=UTC))],
                      date(2025, 1, 15), date(2025, 1, 20))
        assert line == "2025-01-15 12:00 - 13:00: Lunch"

    def test_a_multi_day_event_writes_both_dates(self):
        line = render([_event("Conference", datetime(2025, 6, 15, 8, 0, tzinfo=UTC), datetime(2025, 6, 17, 11, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert line == "2025-06-15 10:00 - 2025-06-17 13:00: Conference"

    def test_an_all_day_event_spans_its_days_as_midnight_utc(self):
        # The CalDAV backend hands a plain date over as its midnight UTC; the line shows that
        # instant in local time, as it always has.
        line = render([_event("Holiday", datetime(2025, 6, 17, tzinfo=UTC), datetime(2025, 6, 18, tzinfo=UTC), all_day=True)],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert line == "2025-06-17 02:00 - 2025-06-18 02:00: Holiday"

    def test_events_come_in_start_order_whatever_the_input_order(self):
        text = render([_event("Afternoon", datetime(2025, 6, 15, 14, 0, tzinfo=UTC), datetime(2025, 6, 15, 15, 0, tzinfo=UTC)),
                       _event("Morning", datetime(2025, 6, 15, 10, 0, tzinfo=UTC), datetime(2025, 6, 15, 11, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert text.index("Morning") < text.index("Afternoon")

    def test_an_event_touching_the_window_by_its_end_is_shown(self):
        text = render([_event("Overnight", datetime(2025, 6, 14, 22, 0, tzinfo=UTC), datetime(2025, 6, 15, 2, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert "Overnight" in text

    def test_an_event_outside_the_window_is_left_out(self):
        text = render([_event("Old", datetime(2025, 6, 10, 10, 0, tzinfo=UTC), datetime(2025, 6, 10, 11, 0, tzinfo=UTC)),
                       _event("Now", datetime(2025, 6, 16, 10, 0, tzinfo=UTC), datetime(2025, 6, 16, 11, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert "Now" in text and "Old" not in text

    def test_a_newline_in_a_title_becomes_a_space(self):
        text = render([_event("Line1\nLine2", datetime(2025, 6, 15, 10, 0, tzinfo=UTC), datetime(2025, 6, 15, 11, 0, tzinfo=UTC))],
                      date(2025, 6, 15), date(2025, 6, 20))
        assert "Line1 Line2" in text and "\n" not in text

    def test_nothing_in_the_window_is_the_no_events_text(self):
        assert render([], date(2025, 6, 15), date(2025, 6, 20)) == NO_EVENTS
