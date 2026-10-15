from datetime import datetime

import pytest
from assistant.tools.MessageFormatter import MessageFormatter


@pytest.mark.unit
class TestMessageFormatterTimestamp:
    """Tests for format_timestamp() — HH:MM from Unix timestamp"""

    def test_morning(self):
        ts = datetime(2025, 3, 15, 9, 5, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "09:05"

    def test_midnight(self):
        ts = datetime(2025, 3, 15, 0, 0, 0).timestamp()
        assert MessageFormatter.format_timestamp(ts) == "00:00"

@pytest.mark.unit
class TestMessageFormatterTimeShort:
    """Tests for format_time_short() — minutes/hours without 'ago'"""

    def test_zero_seconds(self):
        assert MessageFormatter.format_time_short(0) == "0m"

    def test_59_seconds(self):
        assert MessageFormatter.format_time_short(59) == "0m"

    def test_60_seconds(self):
        assert MessageFormatter.format_time_short(60) == "1m"

    def test_120_seconds(self):
        assert MessageFormatter.format_time_short(120) == "2m"

    def test_3599_seconds(self):
        assert MessageFormatter.format_time_short(3599) == "59m"

    def test_one_hour(self):
        assert MessageFormatter.format_time_short(3600) == "1h 0m"

    def test_one_hour_one_minute(self):
        assert MessageFormatter.format_time_short(3660) == "1h 1m"

    def test_24_hours(self):
        assert MessageFormatter.format_time_short(86400) == "24h 0m"


@pytest.mark.unit
class TestMessageFormatterTimeAgo:
    """Tests for format_time_ago() — delegates to format_time_short + ' ago'"""

    def test_zero_seconds(self):
        assert MessageFormatter.format_time_ago(0) == "0m ago"

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

    def test_either_of_two_voices_keeps_its_or(self):
        result = MessageFormatter.format_header("charlotte or frances", None, None, None)
        assert result == "Charlotte or Frances"

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

@pytest.mark.unit
class TestMessageFormatterMessage:
    """Tests for format_message() content formatting"""

    _ts = datetime(2025, 3, 15, 14, 40, 0).timestamp()

    def test_simple_message(self):
        result = MessageFormatter.format_message("Marcus", None, None, None, "Hello")
        assert result == "Marcus: Hello"

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
