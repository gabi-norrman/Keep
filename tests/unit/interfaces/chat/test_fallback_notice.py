"""The fallback notice follows the console's debug toggle on every chat."""
from unittest.mock import patch

import pytest

from interfaces.chat import FallbackNotice

NOTICE = "(Fable refused this request, had to use Opus fallback)"


@pytest.mark.unit
class TestFallbackNotice:
    def test_console_debug_reads_the_home_tab_flag(self):
        with patch('interfaces.chat.FallbackNotice.TUIState.load', return_value={"show_debug": True}) as load:
            assert FallbackNotice.console_debug() is True
        load.assert_called_once_with("home")
        with patch('interfaces.chat.FallbackNotice.TUIState.load', return_value={}):
            assert FallbackNotice.console_debug() is False

    def test_visible_strips_the_notice_only_with_debug_off(self):
        text = "Sure.\n\n" + NOTICE
        with patch('interfaces.chat.FallbackNotice.console_debug', return_value=False):
            assert FallbackNotice.visible(text) == "Sure."
            assert FallbackNotice.visible("Plain reply") == "Plain reply"
        with patch('interfaces.chat.FallbackNotice.console_debug', return_value=True):
            assert FallbackNotice.visible(text) == text
