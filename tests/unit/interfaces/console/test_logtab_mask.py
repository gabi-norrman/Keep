import pytest
from interfaces.console.tabs.LogTab import LogTab


@pytest.fixture
def tab():
    t = LogTab()
    t.display_height = 20
    return t


class TestComputeMaskedLines:
    def test_filters_to_matching_lines(self, tab):
        tab.log_lines = ["INFO│hello world", "DEBUG│goodbye", "INFO│hello again"]
        tab._plain_lines_valid = False
        tab.mask_active = True
        tab.mask_text = "hello"
        assert tab._compute_masked_lines() == ["INFO│hello world", "INFO│hello again"]

    def test_filters_on_thread_column(self, tab):
        tab.log_lines = [
            "I│2026-06-04 │ mainthread │ started",
            "I│2026-06-04 │ ttsworker  │ synth",
            "I│2026-06-04 │ mainthread │ done",
        ]
        tab._plain_lines_valid = False
        tab.mask_active = True
        tab.mask_text = "ttsworker"
        assert tab._compute_masked_lines() == ["I│2026-06-04 │ ttsworker  │ synth"]

    def test_case_insensitive(self, tab):
        tab.log_lines = ["ERROR│Something Failed", "info│ok"]
        tab._plain_lines_valid = False
        tab.mask_active = True
        tab.mask_text = "failed"
        assert tab._compute_masked_lines() == ["ERROR│Something Failed"]

    def test_strips_ansi_when_matching(self, tab):
        tab.log_lines = ["\x1b[31mERROR\x1b[0m│boom", "INFO│ok"]
        tab._plain_lines_valid = False
        tab.mask_active = True
        tab.mask_text = "error"
        assert tab._compute_masked_lines() == ["\x1b[31mERROR\x1b[0m│boom"]

    def test_inactive_returns_all_lines(self, tab):
        tab.log_lines = ["INFO│hello", "DEBUG│world"]
        tab.mask_active = False
        tab.mask_text = ""
        assert tab._compute_masked_lines() == tab.log_lines

    def test_empty_text_returns_all_lines(self, tab):
        tab.log_lines = ["INFO│hello", "DEBUG│world"]
        tab.mask_active = True
        tab.mask_text = ""
        assert tab._compute_masked_lines() == tab.log_lines

    def test_input_mode_filters_live(self, tab):
        tab.log_lines = ["INFO│hello", "DEBUG│world"]
        tab._plain_lines_valid = False
        tab.mask_input_mode = True
        tab.mask_text = "world"
        assert tab._compute_masked_lines() == ["DEBUG│world"]

    def test_updates_when_new_lines_arrive(self, tab):
        tab.log_lines = ["INFO│hello"]
        tab._plain_lines_valid = False
        tab.mask_active = True
        tab.mask_text = "hello"
        assert tab._compute_masked_lines() == ["INFO│hello"]
        tab.log_lines.append("INFO│hello there")
        tab._plain_lines_valid = False  # invalidated on each log read
        assert tab._compute_masked_lines() == ["INFO│hello", "INFO│hello there"]


class TestMaskInput:
    def test_m_enters_input_mode(self, tab):
        tab.scroll_offset = 5
        assert tab.handle_input("m") is True
        assert tab.mask_input_mode is True
        assert tab.mask_active is False
        assert tab.pre_mask_scroll_offset == 5
        assert tab.scroll_offset == 0

    def test_typing_builds_text(self, tab):
        tab.handle_input("m")
        tab.handle_input("a")
        tab.handle_input("b")
        assert tab.mask_text == "ab"

    def test_backspace(self, tab):
        tab.handle_input("m")
        tab.handle_input("a")
        tab.handle_input("b")
        tab.handle_input("backspace")
        assert tab.mask_text == "a"

    def test_enter_applies_mask(self, tab):
        tab.handle_input("m")
        tab.handle_input("x")
        tab.handle_input("\r")
        assert tab.mask_input_mode is False
        assert tab.mask_active is True
        assert tab.mask_text == "x"

    def test_enter_with_empty_text_does_not_activate(self, tab):
        tab.handle_input("m")
        tab.handle_input("\r")
        assert tab.mask_input_mode is False
        assert tab.mask_active is False

    def test_escape_cancels_input(self, tab):
        tab.scroll_offset = 3
        tab.handle_input("m")  # saves pre_mask_scroll_offset=3, scroll_offset=0
        tab.handle_input("x")
        tab.handle_input("escape")
        assert tab.mask_input_mode is False
        assert tab.mask_active is False
        assert tab.mask_text == ""
        assert tab.scroll_offset == 3

    def test_escape_clears_active_mask(self, tab):
        tab.mask_active = True
        tab.mask_text = "thread"
        assert tab.handle_input("escape") is True
        assert tab.mask_active is False
        assert tab.mask_text == ""

    def test_m_edits_existing_mask_preserving_text(self, tab):
        tab.mask_active = True
        tab.mask_text = "thread"
        tab.handle_input("m")
        assert tab.mask_input_mode is True
        assert tab.mask_text == "thread"


class TestMaskSearchExclusive:
    def test_starting_search_clears_mask(self, tab):
        tab.mask_active = True
        tab.mask_text = "thread"
        tab.handle_input("\x06")  # Ctrl+F
        assert tab.search_mode is True
        assert tab.mask_active is False
        assert tab.mask_text == ""

    def test_starting_mask_clears_search(self, tab):
        tab.search_mode = True
        # Ctrl+F routes through _handle_search_input, so simulate post-search then mask
        tab.search_mode = False
        tab.handle_input("m")
        assert tab.mask_input_mode is True
        assert tab.search_mode is False
