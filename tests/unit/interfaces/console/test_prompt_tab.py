"""PromptTab: `c` asks before wiping today's dialog history, `y` wipes it through
MessageHistory, anything else cancels; the outcome replaces the key hints until the next key."""
from unittest.mock import Mock

import pytest

from interfaces.console.framework.TUIScreen import TUIScreen
from interfaces.console.tabs.PromptTab import PromptTab, CONFIRM_HINT


@pytest.fixture
def prompt_manager():
    pm = Mock()
    pm.prompt = "# Prompt\nhello"
    pm.message_history.last_update_time = 1.0
    pm.message_history.render_for_display.return_value = "**User**: hi"
    pm.message_history.wipe_today.return_value = 4
    return pm


@pytest.fixture
def tab(prompt_manager):
    return PromptTab(prompt_manager)


def bottom_bar(tab):
    screen = TUIScreen(80, 30)
    tab.draw(screen, 0)
    return screen.row_text(1, screen.height - 1, 80).strip()


class TestClear:
    def test_c_asks_first_and_y_wipes(self, tab, prompt_manager):
        assert tab.handle_input('c') is True
        assert bottom_bar(tab) == CONFIRM_HINT.strip()
        prompt_manager.message_history.wipe_today.assert_not_called()

        assert tab.handle_input('y') is True
        prompt_manager.message_history.wipe_today.assert_called_once_with()
        assert bottom_bar(tab) == "Wiped 4 history entries for today"

    def test_any_other_key_cancels(self, tab, prompt_manager):
        tab.handle_input('c')
        assert tab.handle_input('n') is True
        prompt_manager.message_history.wipe_today.assert_not_called()
        assert bottom_bar(tab) == "Clear cancelled"

    def test_the_next_key_restores_the_hints(self, tab):
        tab.handle_input('c')
        tab.handle_input('y')
        tab.handle_input('down')
        assert "c clear" in bottom_bar(tab)
        assert "Wiped" not in bottom_bar(tab)

    def test_leaving_the_tab_disarms_the_question(self, tab, prompt_manager):
        tab.handle_input('c')
        tab.on_deactivate()
        assert tab.handle_input('y') is False   # plain scroll handling again, nothing wiped
        prompt_manager.message_history.wipe_today.assert_not_called()

    def test_wipe_scrolls_back_to_the_end(self, tab):
        tab.scroll_offset = 7
        tab.handle_input('c')
        tab.handle_input('y')
        assert tab.scroll_offset == 0

    def test_failure_is_reported_not_raised(self, tab, prompt_manager):
        prompt_manager.message_history.wipe_today.side_effect = RuntimeError("db locked")
        tab.handle_input('c')
        tab.handle_input('y')
        assert bottom_bar(tab) == "Clear failed: db locked"

    def test_v_shows_the_other_channels_window(self, tab, prompt_manager):
        """The text window is shown until `v` flips to voice's, and the history is rendered
        for the window shown; the hint names the other one."""
        bottom = bottom_bar(tab)
        prompt_manager.message_history.render_for_display.assert_called_with("text")
        assert "v show voice window" in bottom
        assert tab.handle_input('v') is True
        bottom = bottom_bar(tab)
        prompt_manager.message_history.render_for_display.assert_called_with("voice")
        assert "v show text window" in bottom
        assert any("the voice window" in line for line in tab.prompt_lines)

    def test_scroll_keys_still_work(self, tab):
        tab.display_height = 5
        wrapped = tab.line_wrap
        assert tab.handle_input('w') is True
        assert tab.line_wrap is not wrapped
