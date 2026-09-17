"""The completion note on a command echo: "done after 4s, received 345 chars"."""
from unittest.mock import patch

import pytest

from assistant.commands.CommandEcho import describe_completion, describe_duration, describe_result


@pytest.mark.unit
class TestDescribeCompletion:
    def test_seconds_are_whole_once_it_took_a_while(self):
        with patch("assistant.commands.CommandEcho.time.time", return_value=100.0):
            assert describe_completion(95.8) == "done after 4s"
            assert describe_completion(99.7) == "done after 0.3s"

    def test_instant_commands_are_not_timed(self):
        with patch("assistant.commands.CommandEcho.time.time", return_value=100.0):
            assert describe_completion(100.0) == "done"
            assert describe_completion(99.98, "x" * 117) == "done, received 117 chars"
            assert describe_completion(99.99, failed=True) == "failed"
            assert describe_completion(99.9) == "done after 0.1s"

    def test_minutes_once_it_took_over_a_minute(self):
        assert describe_duration(59.4) == "59s"
        assert describe_duration(59.6) == "1m 0s"
        assert describe_duration(72) == "1m 12s"
        assert describe_duration(725) == "12m 5s"
        with patch("assistant.commands.CommandEcho.time.time", return_value=100.0):
            assert describe_completion(1.0) == "done after 1m 39s"

    def test_failure_and_the_text_received(self):
        with patch("assistant.commands.CommandEcho.time.time", return_value=100.0):
            assert describe_completion(98.0, "x" * 345) == "done after 2s, received 345 chars"
            assert describe_completion(98.0, "x" * 1234) == "done after 2s, received 1.2k chars"
            assert describe_completion(98.0, None, failed=True) == "failed after 2s"

    def test_block_lists_count_images_and_text(self):
        blocks = [{"type": "image", "source": {}}, {"type": "text", "text": "abc"}, {"type": "image"}]
        assert describe_result(blocks) == "2 images, 3 chars"
        assert describe_result([{"type": "image"}]) == "1 image"
        assert describe_result("   ") == ""
        assert describe_result(None) == ""
        assert describe_result(42) == ""
