from datetime import datetime

import pytest
from assistant.tools.MessageFormatter import MessageFormatter


@pytest.mark.unit
class TestMessageFormatterTimestamp:
    """Tests for format_timestamp() — HH:MM from Unix timestamp"""

    def test_morning(self):
        ts = datetime(2025, 3, 15, 9, 5, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "09:05"

    def test_afternoon(self):
        ts = datetime(2025, 3, 15, 14, 40, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "14:40"

    def test_midnight(self):
        ts = datetime(2025, 3, 15, 0, 0, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "00:00"

    def test_end_of_day(self):
        ts = datetime(2025, 3, 15, 23, 59, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "23:59"


@pytest.mark.unit
class TestMessageFormatterTimeShort:
    """Tests for format_time_short() — minutes/hours without 'ago'"""

    def test_zero_seconds(self):
        assert MessageFormatter.format_time_short(0) == "0m"

    def test_under_one_minute(self):
        assert MessageFormatter.format_time_short(30) == "0m"

    def test_59_seconds(self):
        assert MessageFormatter.format_time_short(59) == "0m"

    def test_60_seconds(self):
        assert MessageFormatter.format_time_short(60) == "1m"

    def test_90_seconds(self):
        assert MessageFormatter.format_time_short(90) == "1m"

    def test_120_seconds(self):
        assert MessageFormatter.format_time_short(120) == "2m"

    def test_3599_seconds(self):
        assert MessageFormatter.format_time_short(3599) == "59m"

    def test_one_hour(self):
        assert MessageFormatter.format_time_short(3600) == "1h 0m"

    def test_one_hour_one_minute(self):
        assert MessageFormatter.format_time_short(3660) == "1h 1m"

    def test_two_hours(self):
        assert MessageFormatter.format_time_short(7200) == "2h 0m"

    def test_24_hours(self):
        assert MessageFormatter.format_time_short(86400) == "24h 0m"


@pytest.mark.unit
class TestMessageFormatterTimeAgo:
    """Tests for format_time_ago() — delegates to format_time_short + ' ago'"""

    def test_zero_seconds(self):
        assert MessageFormatter.format_time_ago(0) == "0m ago"

    def test_under_one_minute(self):
        assert MessageFormatter.format_time_ago(30) == "0m ago"

    def test_59_seconds(self):
        assert MessageFormatter.format_time_ago(59) == "0m ago"

    def test_60_seconds_becomes_1_minute(self):
        assert MessageFormatter.format_time_ago(60) == "1m ago"

    def test_61_seconds_still_1_minute(self):
        assert MessageFormatter.format_time_ago(61) == "1m ago"

    def test_119_seconds_still_1_minute(self):
        assert MessageFormatter.format_time_ago(119) == "1m ago"

    def test_120_seconds_becomes_2_minutes(self):
        assert MessageFormatter.format_time_ago(120) == "2m ago"

    def test_3599_seconds_is_59_minutes(self):
        assert MessageFormatter.format_time_ago(3599) == "59m ago"

    def test_3600_seconds_becomes_1_hour(self):
        assert MessageFormatter.format_time_ago(3600) == "1h 0m ago"

    def test_3660_seconds_is_1_hour_1_minute(self):
        assert MessageFormatter.format_time_ago(3660) == "1h 1m ago"

    def test_7200_seconds_is_2_hours(self):
        assert MessageFormatter.format_time_ago(7200) == "2h 0m ago"

    def test_large_value_24_hours(self):
        assert MessageFormatter.format_time_ago(86400) == "24h 0m ago"

    def test_fractional_seconds_truncated(self):
        assert MessageFormatter.format_time_ago(30.7) == "0m ago"

    def test_fractional_minutes_truncated(self):
        assert MessageFormatter.format_time_ago(90.9) == "1m ago"


@pytest.mark.unit
class TestMessageFormatterHeader:
    """Tests for format_header() with various parameter combinations"""

    _ts = datetime(2025, 3, 15, 14, 40, 0).timestamp()

    def test_all_none_returns_user(self):
        result = MessageFormatter.format_header(None, None, None, None)
        assert result == "User"

    def test_user_only(self):
        result = MessageFormatter.format_header("Marcus", None, None, None)
        assert result == "Marcus"

    def test_user_and_timestamp(self):
        result = MessageFormatter.format_header("Marcus", self._ts, None, None)
        assert result == "Marcus (14:40)"

    def test_user_and_source(self):
        result = MessageFormatter.format_header("Marcus", None, "VoiceToVoice", None)
        assert result == "Marcus (VoiceToVoice)"

    def test_user_source_and_location(self):
        result = MessageFormatter.format_header("Marcus", None, "VoiceToVoice", "Kitchen")
        assert result == "Marcus (VoiceToVoice in Kitchen)"

    def test_user_timestamp_and_source(self):
        result = MessageFormatter.format_header("Marcus", self._ts, "Telegram", None)
        assert result == "Marcus (14:40, Telegram)"

    def test_all_parameters(self):
        result = MessageFormatter.format_header("Marcus", self._ts, "VoiceToVoice", "Kitchen")
        assert result == "Marcus (14:40, VoiceToVoice in Kitchen)"

    def test_none_user_with_other_params(self):
        result = MessageFormatter.format_header(None, self._ts, "VoiceToVoice", None)
        assert result == "User (14:40, VoiceToVoice)"

    def test_location_without_source_ignored(self):
        result = MessageFormatter.format_header("Marcus", None, None, "Kitchen")
        assert result == "Marcus"

    def test_morning_timestamp(self):
        ts = datetime(2025, 3, 15, 9, 18, 0).timestamp()
        result = MessageFormatter.format_header("Charlotte", ts, "Telegram", None)
        assert result == "Charlotte (09:18, Telegram)"


@pytest.mark.unit
class TestMessageFormatterMessage:
    """Tests for format_message() content formatting"""

    _ts = datetime(2025, 3, 15, 14, 40, 0).timestamp()

    def test_simple_message(self):
        result = MessageFormatter.format_message("Marcus", None, None, None, "Hello")
        assert result == "Marcus: Hello"

    def test_message_with_all_params(self):
        result = MessageFormatter.format_message(
            "Marcus", self._ts, "VoiceToVoice", "Kitchen", "turn off the lights"
        )
        assert result == "Marcus (14:40, VoiceToVoice in Kitchen): turn off the lights"

    def test_message_with_timestamp_only(self):
        result = MessageFormatter.format_message("User", self._ts, None, None, "What's up?")
        assert result == "User (14:40): What's up?"

    def test_empty_content(self):
        result = MessageFormatter.format_message("Marcus", None, None, None, "")
        assert result == "Marcus: "

    def test_multiline_content(self):
        result = MessageFormatter.format_message("User", None, None, None, "Line 1\nLine 2")
        assert result == "User: Line 1\nLine 2"

    def test_content_with_special_chars(self):
        result = MessageFormatter.format_message("User", None, None, None, "What's 2+2?")
        assert result == "User: What's 2+2?"


@pytest.mark.unit
class TestMessageFormatterAssistantHeader:
    """Tests for format_assistant_header() with/without time"""

    def test_no_time(self):
        result = MessageFormatter.format_assistant_header(None)
        assert result == "Assistant"

    def test_with_timestamp(self):
        ts = datetime(2025, 3, 15, 14, 40, 0).timestamp()
        result = MessageFormatter.format_assistant_header(ts)
        assert result == "Assistant (14:40)"

    def test_morning_timestamp(self):
        ts = datetime(2025, 3, 15, 9, 5, 0).timestamp()
        result = MessageFormatter.format_assistant_header(ts)
        assert result == "Assistant (09:05)"
