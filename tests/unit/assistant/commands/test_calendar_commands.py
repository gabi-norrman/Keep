"""The calendar commands, core ones since several integrations can be behind them: thin
delegates to CalendarAccess that return the reason as the command's error."""
from unittest.mock import Mock, patch

import pytest

from assistant.commands.Commands import Commands

CURRENT = "assistant.commands.Commands.CalendarAccess.current"


@pytest.fixture
def commands():
    return Commands(Mock(command_reply=None, command_reply_contains_error=False))


@pytest.mark.unit
class TestCreateEvent:
    def test_an_under_quoted_title_is_rejoined_and_the_last_three_tokens_are_the_times(self, commands):
        # Simulates LLM under-quoting: a title containing a comma got split into
        # multiple tokens at parse time. The handler re-joins leading tokens.
        with patch(CURRENT) as current:
            result = commands._run_create_event("Dinner with Bob", "Carol", "2025-10-28", "18:00", "19:00")
        current.return_value.create.assert_called_once_with("Dinner with Bob, Carol", "2025-10-28", "18:00", "19:00", "")
        assert result == ""

    def test_a_fifth_argument_after_the_end_time_names_the_calendar(self, commands):
        with patch(CURRENT) as current:
            commands._run_create_event("Football", "2025-10-28", "16:00", "17:00", '"Kids"')
        current.return_value.create.assert_called_once_with("Football", "2025-10-28", "16:00", "17:00", "Kids")

    def test_an_under_quoted_title_with_a_calendar_still_splits_at_the_times(self, commands):
        with patch(CURRENT) as current:
            commands._run_create_event("Dinner with Bob", "Carol", "2025-10-28", "18:00", "19:00", "Work/Calendar")
        current.return_value.create.assert_called_once_with("Dinner with Bob, Carol", "2025-10-28", "18:00", "19:00", "Work/Calendar")

    def test_too_few_parameters_or_a_refusing_calendar_is_a_failure_message(self, commands):
        assert "create_event failed" in commands._run_create_event("Dentist", "2025-10-28")
        with patch(CURRENT) as current:
            current.return_value.create.side_effect = ValueError("bad date")
            result = commands._run_create_event("Dentist", "bad-date", "10:00", "11:00")
        assert "create_event failed" in result and "bad date" in result

    def test_runs_in_the_background_and_takes_several_parameters(self, commands):
        commands.background_command = Mock()
        commands.cmd_create_event("Dentist", "2025-10-28", "10:00", "11:00")
        function, params = commands.background_command.call_args.args[:2]
        assert params == ["Dentist", "2025-10-28", "10:00", "11:00"] and function == commands._run_create_event
        assert commands.is_multi_param("create_event")
        assert commands.describe("create_event", "Dentist, 2025-10-28, 10:00, 11:00") == "Creating calendar event 'Dentist'"


@pytest.mark.unit
class TestDeleteEvent:
    def test_the_title_is_everything_before_the_date_and_a_third_token_names_the_calendar(self, commands):
        with patch(CURRENT) as current:
            current.return_value.delete.return_value = "Deleted 'Dinner with Bob, Carol' (...) from iCloud/Home"
            result = commands._run_delete_event("Dinner with Bob", "Carol", "2025-10-28", '"Kids"')
        current.return_value.delete.assert_called_once_with("Dinner with Bob, Carol", "2025-10-28", "Kids")
        assert result.startswith("Deleted")

    def test_without_a_calendar_the_composite_searches_the_shown_ones(self, commands):
        with patch(CURRENT) as current:
            commands._run_delete_event("Dentist", "2025-10-28")
        current.return_value.delete.assert_called_once_with("Dentist", "2025-10-28", "")

    def test_a_missing_title_or_date_too_many_tokens_or_a_refusing_composite_is_a_failure_message(self, commands):
        with patch(CURRENT) as current:
            assert "delete_event failed" in commands._run_delete_event("2025-10-28")
            assert "delete_event failed" in commands._run_delete_event("Dentist", "tomorrow")
            assert "delete_event failed" in commands._run_delete_event("Dentist", "2025-10-28", "Kids", "extra")
            current.return_value.delete.assert_not_called()
            current.return_value.delete.side_effect = ValueError("matches several events")
            result = commands._run_delete_event("Dentist", "2025-10-28")
        assert "delete_event failed" in result and "several" in result

    def test_runs_in_the_background_and_takes_several_parameters(self, commands):
        commands.background_command = Mock()
        commands.cmd_delete_event("Dentist", "2025-10-28")
        function, params = commands.background_command.call_args.args[:2]
        assert params == ["Dentist", "2025-10-28"] and function == commands._run_delete_event
        assert commands.is_multi_param("delete_event")
        assert commands.describe("delete_event", '"Dentist", 2025-10-28') == "Deleting calendar event 'Dentist'"


@pytest.mark.unit
class TestMoveEvent:
    def test_two_dates_then_both_times_then_the_calendar(self, commands):
        with patch(CURRENT) as current:
            current.return_value.move.return_value = "Moved ..."
            result = commands._run_move_event("Dinner with Bob", "Carol", "2025-10-28", "2025-10-30", "18:00", "19:30", "Kids")
        current.return_value.move.assert_called_once_with("Dinner with Bob, Carol", "2025-10-28", "2025-10-30", "18:00", "19:30", "Kids")
        assert result == "Moved ..."

    def test_without_times_the_event_keeps_its_own_and_the_third_token_is_the_calendar(self, commands):
        with patch(CURRENT) as current:
            commands._run_move_event("Dentist", "2025-10-28", "2025-10-30")
            commands._run_move_event("Dentist", "2025-10-28", "2025-10-30", '"Work/Calendar"')
        assert [c.args for c in current.return_value.move.call_args_list] == [
            ("Dentist", "2025-10-28", "2025-10-30", "", "", ""),
            ("Dentist", "2025-10-28", "2025-10-30", "", "", "Work/Calendar")]

    def test_one_time_alone_a_missing_new_date_or_a_refusing_composite_is_a_failure_message(self, commands):
        with patch(CURRENT) as current:
            assert "move_event failed" in commands._run_move_event("Dentist", "2025-10-28")
            assert "move_event failed" in commands._run_move_event("Dentist", "2025-10-28", "2025-10-30", "10:00")
            assert "move_event failed" in commands._run_move_event("Dentist", "2025-10-28", "2025-10-30", "10:00", "11:00", "Kids", "x")
            current.return_value.move.assert_not_called()
            current.return_value.move.side_effect = ValueError("no event titled")
            assert "no event titled" in commands._run_move_event("Dentist", "2025-10-28", "2025-10-30")

    def test_runs_in_the_background_and_takes_several_parameters(self, commands):
        commands.background_command = Mock()
        commands.cmd_move_event("Dentist", "2025-10-28", "2025-10-30")
        function, params = commands.background_command.call_args.args[:2]
        assert params == ["Dentist", "2025-10-28", "2025-10-30"] and function == commands._run_move_event
        assert commands.is_multi_param("move_event")
        assert commands.describe("move_event", "Dentist, 2025-10-28, 2025-10-30") == "Moving calendar event 'Dentist'"


@pytest.mark.unit
class TestLoadCalendar:
    def test_loads_the_named_calendars_through_the_composite(self, commands):
        with patch(CURRENT) as current:
            current.return_value.load.return_value = "Calendar 'Work', ...:\n(No upcoming events)"
            result = commands._run_load_calendar("Work", "", "Home")
        current.return_value.load.assert_called_once_with(["Work", "Home"])
        assert result.startswith("Calendar 'Work'")

    def test_no_name_or_a_broken_account_is_a_failure_message(self, commands):
        assert "failed" in commands._run_load_calendar()
        with patch(CURRENT) as current:
            current.return_value.load.side_effect = ValueError("no login")
            assert "no login" in commands._run_load_calendar("Work")

    def test_is_pure_so_the_prompt_suite_still_runs_it(self, commands):
        assert commands.is_pure("load_calendar") and commands.is_multi_param("load_calendar")
        assert commands.describe("load_calendar", '"Work"') == "Looking at the calendar: Work"
