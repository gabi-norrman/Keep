import pytest
from interfaces.console.tabs.LogTab import LogTab


@pytest.fixture
def tab():
    t = LogTab()
    t.display_height = 20
    return t


class TestPerformSearch:
    def test_finds_matching_lines(self, tab):
        tab.log_lines = ["INFO│hello world", "DEBUG│goodbye", "INFO│hello again"]
        tab._plain_lines_valid = False
        tab.search_text = "hello"
        tab._perform_search()
        assert tab.search_matches == [0, 2]
        assert tab.search_current_idx == 1

    def test_case_insensitive(self, tab):
        tab.log_lines = ["ERROR│Something Failed", "info│ok"]
        tab._plain_lines_valid = False
        tab.search_text = "failed"
        tab._perform_search()
        assert tab.search_matches == [0]

    def test_no_matches(self, tab):
        tab.log_lines = ["INFO│hello"]
        tab._plain_lines_valid = False
        tab.search_text = "zzz"
        tab._perform_search()
        assert tab.search_matches == []
        assert tab.search_current_idx == -1

    def test_empty_search_text(self, tab):
        tab.log_lines = ["INFO│hello"]
        tab._plain_lines_valid = False
        tab.search_text = ""
        tab._perform_search()
        assert tab.search_matches == []

    def test_strips_ansi_before_matching(self, tab):
        tab.log_lines = ["\x1b[31mERROR\x1b[0m│something broke"]
        tab._plain_lines_valid = False
        tab.search_text = "error"
        tab._perform_search()
        assert tab.search_matches == [0]


class TestFormatLineForWidth:
    def test_wide_terminal_keeps_all_columns(self):
        tab = LogTab()
        line = "INFO│12:00:00│main│hello world"
        assert tab._format_line_for_width(line, 170) == line

    def test_medium_terminal_removes_thread(self):
        tab = LogTab()
        line = "INFO│12:00:00│main│hello world"
        result = tab._format_line_for_width(line, 140)
        assert result == "INFO│12:00:00│hello world"

    def test_narrow_terminal_keeps_level_and_message(self):
        tab = LogTab()
        line = "INFO│12:00:00│main│hello world"
        result = tab._format_line_for_width(line, 100)
        assert result == "INFO│hello world"

    def test_non_standard_format_unchanged(self):
        tab = LogTab()
        line = "plain text without separators"
        assert tab._format_line_for_width(line, 100) == line


class TestTruncateAnsiAware:
    def test_plain_text_truncation(self):
        tab = LogTab()
        assert tab._truncate_ansi_aware("hello world", 5) == "hello"

    def test_preserves_ansi_codes(self):
        tab = LogTab()
        line = "\x1b[31mhello\x1b[0m world"
        result = tab._truncate_ansi_aware(line, 5)
        # Truncation stops after 5 visible chars; trailing reset is past the limit
        assert result == "\x1b[31mhello"

    def test_ansi_in_middle(self):
        tab = LogTab()
        line = "ab\x1b[32mcd\x1b[0mef"
        result = tab._truncate_ansi_aware(line, 4)
        assert result == "ab\x1b[32mcd"

    def test_ansi_codes_dont_count_as_visible(self):
        tab = LogTab()
        line = "\x1b[31ma\x1b[32mb\x1b[33mc"
        result = tab._truncate_ansi_aware(line, 3)
        assert result == "\x1b[31ma\x1b[32mb\x1b[33mc"

    def test_no_truncation_needed(self):
        tab = LogTab()
        assert tab._truncate_ansi_aware("hi", 10) == "hi"


class TestHighlightSearch:
    def test_highlight_current_match(self):
        tab = LogTab()
        line = "\x1b[31mERR\x1b[0m│something"
        result = tab._highlight_match(line, "err", is_current=True)
        assert '\x1b[7m' in result

    def test_highlight_non_current_match(self):
        tab = LogTab()
        line = "\x1b[31mERR\x1b[0m│something"
        result = tab._highlight_match(line, "err", is_current=False)
        assert '\x1b[33;1m' in result

    def test_no_match_returns_unchanged(self):
        tab = LogTab()
        line = "INFO│hello"
        assert tab._highlight_match(line, "zzz", is_current=True) == line


class TestScrollToMatch:
    def test_scrolls_to_center_match(self, tab):
        tab.log_lines = [f"line{i}" for i in range(100)]
        tab.display_height = 20
        tab._scroll_to_match(50)
        assert tab.scroll_offset == 40

    def test_clamps_to_zero(self, tab):
        tab.log_lines = [f"line{i}" for i in range(100)]
        tab.display_height = 20
        tab._scroll_to_match(95)
        assert tab.scroll_offset >= 0

    def test_clamps_to_max_scroll(self, tab):
        tab.log_lines = [f"line{i}" for i in range(100)]
        tab.display_height = 20
        tab._scroll_to_match(5)
        assert tab.scroll_offset <= 80


def _strip_ansi(s):
    return LogTab.ANSI_RE.sub('', s)


class TestWrapAnsiAware:
    def test_short_line_returned_unchanged(self):
        tab = LogTab()
        assert tab._wrap_ansi_aware("hello", 80) == ["hello"]

    def test_plain_line_wraps_at_word_boundary(self):
        tab = LogTab()
        assert tab._wrap_ansi_aware("hello world", 5) == ["hello", "world"]

    def test_visible_width_ignores_ansi_codes(self):
        tab = LogTab()
        # 11 visible chars but the line is much longer due to color codes.
        line = "\x1b[31mhello world\x1b[0m"
        result = tab._wrap_ansi_aware(line, 5)
        assert len(result) == 2
        # Stripped of codes, the wrap matches the plain-text wrap.
        assert [_strip_ansi(s) for s in result] == ["hello", "world"]

    def test_active_color_carried_across_wrap(self):
        tab = LogTab()
        line = "\x1b[31mhello world\x1b[0m"
        result = tab._wrap_ansi_aware(line, 5)
        # The red code opened on line 1 is re-applied at the start of line 2.
        assert result[0].startswith("\x1b[31m")
        assert result[1].startswith("\x1b[31m")

    def test_code_in_middle_carries_to_next_line(self):
        tab = LogTab()
        line = "ab\x1b[32mcd ef\x1b[0m"
        result = tab._wrap_ansi_aware(line, 4)
        assert [_strip_ansi(s) for s in result] == ["abcd", "ef"]
        # The green code opened mid-first-line is active at the start of line 2.
        assert result[1].startswith("\x1b[32m")


class TestCapturesText:
    """The TUI hands every key (including '/' and '|') to the tab only while a field is open."""

    def test_search_field_captures_until_closed(self, tab):
        assert tab.captures_text is False
        tab.handle_input('\x06')
        assert tab.captures_text is True
        tab.handle_input('/')
        assert tab.search_text == "/"
        tab.handle_input('enter')
        assert tab.captures_text is False

    def test_mask_field_captures_until_escaped(self, tab):
        tab.handle_input('m')
        assert tab.captures_text is True
        tab.handle_input('escape')
        assert tab.captures_text is False

    def test_an_active_mask_does_not_capture(self, tab):
        tab.handle_input('m')
        tab.handle_input('x')
        tab.handle_input('enter')
        assert tab.mask_active is True
        assert tab.captures_text is False
